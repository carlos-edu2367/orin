from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GPUDeviceInfo:
    has_cuda: bool
    device_name: str | None = None
    total_vram_gb: float = 0.0
    free_vram_gb: float = 0.0
    cpu_cores: int = 4
    ram_gb: float = 8.0


@dataclass(frozen=True, slots=True)
class VoiceHardwareProfile:
    tier: str
    preset: str
    has_cuda: bool
    device_name: str | None
    total_vram_gb: float
    free_vram_gb: float
    estimated_vram_gb: float
    primary_tts_provider: str
    primary_tts_model: str
    alternative_tts_model: str
    recommended_stt_provider: str
    recommended_stt_model: str
    stt_compute_type: str
    vad_engine: str = "silero-vad-v5-onnx"
    fallback_applied: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def preset_id(self) -> str:
        return self.preset

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        data["preset_id"] = self.preset
        return data


def _default_hardware_probe() -> GPUDeviceInfo:
    cpu_cores = os.cpu_count() or 4
    ram_gb = 16.0

    # 1. Tenta via torch.cuda se disponível e já instalado
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            total_b = float(getattr(props, "total_memory", 0))
            free_b, total_info_b = torch.cuda.mem_get_info(0)
            total_gb = round((total_b or float(total_info_b)) / (1024**3), 2)
            free_gb = round(float(free_b) / (1024**3), 2)
            return GPUDeviceInfo(
                has_cuda=True,
                device_name=str(getattr(props, "name", "NVIDIA CUDA GPU")),
                total_vram_gb=total_gb,
                free_vram_gb=free_gb,
                cpu_cores=cpu_cores,
                ram_gb=ram_gb,
            )
    except Exception:
        pass

    # 2. Tenta via nvidia-smi sem abrir janela de console no Windows
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            creationflags=creationflags,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 3:
                name = parts[0]
                total_mb = float(parts[1])
                free_mb = float(parts[2])
                return GPUDeviceInfo(
                    has_cuda=True,
                    device_name=name,
                    total_vram_gb=round(total_mb / 1024.0, 2),
                    free_vram_gb=round(free_mb / 1024.0, 2),
                    cpu_cores=cpu_cores,
                    ram_gb=ram_gb,
                )
    except Exception:
        pass

    return GPUDeviceInfo(
        has_cuda=False,
        device_name=None,
        total_vram_gb=0.0,
        free_vram_gb=0.0,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
    )


class VoiceHardwareGovernor:
    """Governador adaptativo de hardware para o Modo LIVE do Orin.
    - Detecta GPUs de 8GB+ (ex.: RTX 4060 8GB) e recomenda `Chatterbox-Multilingual-pt-br` (500M fp16)
      ou `F5-TTS-pt-br` (335M fp16) + `faster-whisper-large-v3-turbo` (`int8_float16`) (~3.4 GB VRAM).
    - Faz fallback automático para `kokoro-82m-onnx` + `faster-whisper-small-int8` se a VRAM livre
      cair abaixo de 1.8 GB ou em ambientes CPU-only, evitando crashes por CUDA OOM.
    """

    HIGH_NATURALNESS_MIN_TOTAL_VRAM_GB: float = 6.0
    HIGH_NATURALNESS_MIN_FREE_VRAM_GB: float = 4.5
    CRITICAL_MIN_FREE_VRAM_GB: float = 1.8
    ESTIMATED_HIGH_PRESET_VRAM_GB: float = 3.4
    ESTIMATED_HYBRID_PRESET_VRAM_GB: float = 1.6
    ESTIMATED_CPU_PRESET_VRAM_GB: float = 0.0

    def __init__(
        self,
        probe_fn: Callable[[], GPUDeviceInfo] | None = None,
    ) -> None:
        self._probe_fn = probe_fn or _default_hardware_probe
        self._forced_cpu_fallback: bool = False
        self._last_oom_model: str | None = None

    def probe(self) -> GPUDeviceInfo:
        try:
            return self._probe_fn()
        except Exception:
            return GPUDeviceInfo(has_cuda=False, device_name=None)

    def recommend(self) -> VoiceHardwareProfile:
        info = self.probe()

        if self._forced_cpu_fallback:
            return VoiceHardwareProfile(
                tier="cpu_only" if not info.has_cuda else "local_low_vram",
                preset="cpu_lightweight_fallback",
                has_cuda=info.has_cuda,
                device_name=info.device_name,
                total_vram_gb=info.total_vram_gb,
                free_vram_gb=info.free_vram_gb,
                estimated_vram_gb=self.ESTIMATED_CPU_PRESET_VRAM_GB,
                primary_tts_provider="local_sidecar",
                primary_tts_model="kokoro-82m-onnx",
                alternative_tts_model="kokoro-82m-onnx",
                recommended_stt_provider="local_sidecar",
                recommended_stt_model="faster-whisper-small-int8",
                stt_compute_type="int8",
                fallback_applied=True,
                warnings=(
                    f"Fallback automático para CPU/ONNX ativado após pressão de VRAM ({self._last_oom_model or 'CUDA OOM'}).",
                ),
            )

        if not info.has_cuda or info.total_vram_gb <= 0.0:
            return VoiceHardwareProfile(
                tier="cpu_only",
                preset="cpu_lightweight",
                has_cuda=False,
                device_name=None,
                total_vram_gb=0.0,
                free_vram_gb=0.0,
                estimated_vram_gb=self.ESTIMATED_CPU_PRESET_VRAM_GB,
                primary_tts_provider="local_sidecar",
                primary_tts_model="kokoro-82m-onnx",
                alternative_tts_model="eleven_flash_v2_5",
                recommended_stt_provider="local_sidecar",
                recommended_stt_model="faster-whisper-small-int8",
                stt_compute_type="int8",
                fallback_applied=True,
                warnings=(
                    "GPU CUDA não detectada; usando Kokoro-82M ONNX + Faster-Whisper Small INT8 em CPU.",
                ),
            )

        # VRAM livre crítica (< 1.8 GB): ex. jogo aberto ou modelo grande no Ollama
        if info.free_vram_gb < self.CRITICAL_MIN_FREE_VRAM_GB:
            return VoiceHardwareProfile(
                tier="local_low_vram",
                preset="cpu_lightweight_fallback",
                has_cuda=True,
                device_name=info.device_name,
                total_vram_gb=info.total_vram_gb,
                free_vram_gb=info.free_vram_gb,
                estimated_vram_gb=self.ESTIMATED_CPU_PRESET_VRAM_GB,
                primary_tts_provider="local_sidecar",
                primary_tts_model="kokoro-82m-onnx",
                alternative_tts_model="eleven_flash_v2_5",
                recommended_stt_provider="local_sidecar",
                recommended_stt_model="faster-whisper-small-int8",
                stt_compute_type="int8",
                fallback_applied=True,
                warnings=(
                    f"VRAM livre insuficiente ({info.free_vram_gb:.1f} GB < {self.CRITICAL_MIN_FREE_VRAM_GB:.1f} GB); "
                    "alternando automaticamente para Kokoro-82M ONNX + Faster-Whisper Small INT8 para evitar CUDA OOM.",
                ),
            )

        # GPU 8GB+ com >= 4.5GB livres (ex.: RTX 4060 8GB)
        if (
            info.total_vram_gb >= self.HIGH_NATURALNESS_MIN_TOTAL_VRAM_GB
            and info.free_vram_gb >= self.HIGH_NATURALNESS_MIN_FREE_VRAM_GB
        ):
            return VoiceHardwareProfile(
                tier="local_gpu_8gb",
                preset="local_high_naturalness",
                has_cuda=True,
                device_name=info.device_name,
                total_vram_gb=info.total_vram_gb,
                free_vram_gb=info.free_vram_gb,
                estimated_vram_gb=self.ESTIMATED_HIGH_PRESET_VRAM_GB,
                primary_tts_provider="local_sidecar",
                primary_tts_model="chatterbox-pt-br",
                alternative_tts_model="f5-tts-pt-br",
                recommended_stt_provider="local_sidecar",
                recommended_stt_model="faster-whisper-large-v3-turbo",
                stt_compute_type="int8_float16",
                fallback_applied=False,
                warnings=(),
            )

        # Caso intermediário (1.8 GB <= free_vram_gb < 4.5 GB): mantém STT turbo e usa Kokoro ONNX no TTS
        return VoiceHardwareProfile(
            tier="local_gpu_constrained",
            preset="local_hybrid_balanced",
            has_cuda=True,
            device_name=info.device_name,
            total_vram_gb=info.total_vram_gb,
            free_vram_gb=info.free_vram_gb,
            estimated_vram_gb=self.ESTIMATED_HYBRID_PRESET_VRAM_GB,
            primary_tts_provider="local_sidecar",
            primary_tts_model="kokoro-82m-onnx",
            alternative_tts_model="f5-tts-pt-br",
            recommended_stt_provider="local_sidecar",
            recommended_stt_model="faster-whisper-large-v3-turbo",
            stt_compute_type="int8_float16",
            fallback_applied=True,
            warnings=(
                f"VRAM parcialmente ocupada ({info.free_vram_gb:.1f} GB livres de {info.total_vram_gb:.1f} GB); "
                "usando Kokoro-82M ONNX no TTS para preservar margem de segurança.",
            ),
        )

    def recover_from_oom(self, failed_model: str | None = None) -> VoiceHardwareProfile:
        self._forced_cpu_fallback = True
        self._last_oom_model = failed_model or "cuda_model"
        return self.recommend()


__all__ = [
    "GPUDeviceInfo",
    "VoiceHardwareGovernor",
    "VoiceHardwareProfile",
]
