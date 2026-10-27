import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
// Type-only: the runtime module is loaded lazily inside the factory.
import type * as THREE from 'three'
import { useCanvasScene, type SceneHandle } from '../../../components/three/useCanvasScene'
import { canAnimateScene } from '../../../components/three/webgl'
import type { LiveVoicePhase } from '../activityTypes'
import { orbTheme, type OrbThemeId } from './orbThemes'

export type LiveOrbProps = {
  phase: LiveVoicePhase
  theme: OrbThemeId | string
  /** 0..1 loudness to react to (the person while they talk, the companion while it speaks). */
  level: () => number
  size?: number
  /** Force the CSS orb (tests, reduced motion, no WebGL). */
  staticMode?: boolean
}

// Ashima Arts 3D simplex noise (MIT), the standard compact GLSL version.
const NOISE_GLSL = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.0-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;vec4 s1=floor(b1)*2.0+1.0;vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`

const CORE_VERTEX = /* glsl */ `
uniform float uTime; uniform float uAmp; uniform float uWobble;
varying vec3 vNormal; varying vec3 vView; varying float vNoise;
${NOISE_GLSL}
void main(){
  float n = snoise(normal * 1.6 + vec3(uTime * 0.35));
  float fine = snoise(normal * 4.0 - vec3(uTime * 0.9)) * 0.25;
  float d = (n + fine) * (0.045 + uWobble * 0.05 + uAmp * 0.12);
  vNoise = n;
  vec3 displaced = position + normal * d;
  vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`

const CORE_FRAGMENT = /* glsl */ `
uniform vec3 uDeep; uniform vec3 uMid; uniform vec3 uBright; uniform float uAmp; uniform float uTime;
varying vec3 vNormal; varying vec3 vView; varying float vNoise;
void main(){
  float fresnel = pow(1.0 - max(dot(vNormal, vView), 0.0), 2.2);
  float band = 0.5 + 0.5 * sin(vNoise * 3.0 + vNormal.y * 2.5 + uTime * 0.6);
  vec3 base = mix(uDeep, uMid, smoothstep(-0.6, 0.8, vNormal.y + vNoise * 0.4));
  base = mix(base, uBright, band * 0.35 + fresnel * 0.55);
  vec3 lightDir = normalize(vec3(-0.45, 0.65, 0.6));
  vec3 halfDir = normalize(lightDir + vView);
  float spec = pow(max(dot(vNormal, halfDir), 0.0), 48.0);
  float diffuse = 0.55 + 0.45 * max(dot(vNormal, lightDir), 0.0);
  vec3 color = base * diffuse + vec3(spec) * 0.42 + uBright * fresnel * (0.35 + uAmp * 0.6);
  gl_FragColor = vec4(color, 1.0);
}
`

const SHELL_VERTEX = /* glsl */ `
uniform float uTime; uniform float uAmp; uniform float uPixelRatio;
attribute float aSeed;
varying float vAlpha;
${NOISE_GLSL}
void main(){
  vec3 dir = normalize(position);
  float wave = snoise(dir * 2.2 + vec3(uTime * 0.5));
  float radius = 1.0 + wave * (0.05 + uAmp * 0.22) + sin(uTime * 2.0 + aSeed * 6.283) * 0.012;
  vec4 mv = modelViewMatrix * vec4(position * radius, 1.0);
  vAlpha = 0.35 + 0.65 * smoothstep(-0.2, 1.0, dir.z) ;
  gl_PointSize = (2.2 + uAmp * 2.4 + aSeed * 1.2) * uPixelRatio * (3.2 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`

const SHELL_FRAGMENT = /* glsl */ `
uniform vec3 uColor; uniform float uOpacity;
varying float vAlpha;
void main(){
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  if (d > 0.5) discard;
  float soft = smoothstep(0.5, 0.0, d);
  gl_FragColor = vec4(uColor, soft * vAlpha * uOpacity);
}
`

type EyeTarget = { open: number; scale: number; lookX: number; lookY: number; squint: number }

function eyeTargetFor(phase: LiveVoicePhase, elapsed: number, level: number): EyeTarget {
  switch (phase) {
    case 'hearing':
      return { open: 1, scale: 1.12, lookX: 0, lookY: 0.01, squint: 0 }
    case 'transcribing':
    case 'thinking':
      return { open: 0.9, scale: 1, lookX: 0.05 * Math.sin(elapsed * 0.9), lookY: 0.06, squint: 0 }
    case 'working':
      return { open: 0.85, scale: 0.96, lookX: 0.06 * Math.sin(elapsed * 1.6), lookY: -0.02, squint: 0.1 }
    case 'speaking':
      return { open: 1, scale: 1 + level * 0.08, lookX: 0, lookY: level * 0.025, squint: 0.35 }
    case 'interrupted':
      return { open: 1, scale: 1.25, lookX: 0, lookY: 0.02, squint: 0 }
    case 'muted':
      return { open: 0.14, scale: 1, lookX: 0, lookY: -0.03, squint: 0 }
    case 'error':
      return { open: 0.6, scale: 0.85, lookX: 0, lookY: -0.03, squint: 0 }
    default:
      return { open: 1, scale: 1, lookX: 0.02 * Math.sin(elapsed * 0.4), lookY: 0, squint: 0 }
  }
}

function hexToRgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.replace('#', ''), 16)
  return [((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255]
}

function CssOrb({ phase, theme, level, size, animate }: { phase: LiveVoicePhase; theme: string; level: () => number; size: number; animate: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const palette = orbTheme(theme)
  useEffect(() => {
    if (!animate) return
    let frame = 0
    const tick = () => {
      ref.current?.style.setProperty('--orb-level', level().toFixed(3))
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [animate, level])
  const style = {
    width: size,
    height: size,
    '--orb-deep': palette.core[0],
    '--orb-mid': palette.core[1],
    '--orb-bright': palette.core[2],
    '--orb-glow': palette.glow,
  } as CSSProperties
  return (
    <div ref={ref} className="live-orb live-orb--css" data-phase={phase} data-animate={animate ? 'true' : 'false'} style={style}>
      <div className="live-orb__body" />
      <div className="live-orb__eyes" aria-hidden="true">
        <span className="live-orb__eye" />
        <span className="live-orb__eye" />
      </div>
    </div>
  )
}

export function LiveOrb({ phase, theme, level, size = 340, staticMode = false }: LiveOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [webgl] = useState(() => !staticMode && canAnimateScene())
  const phaseRef = useRef(phase)
  const levelRef = useRef(level)
  const themeRef = useRef(theme)
  const haloRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => { phaseRef.current = phase }, [phase])
  useEffect(() => { levelRef.current = level }, [level])
  useEffect(() => { themeRef.current = theme }, [theme])

  const factory = useCallback((canvas: HTMLCanvasElement): SceneHandle | null => {
    let disposed = false
    let handle: SceneHandle | null = null
    let pendingSize: [number, number, number] | null = null
    const proxy: SceneHandle = {
      frame: (elapsed, delta) => handle?.frame(elapsed, delta),
      resize: (width, height, ratio) => {
        pendingSize = [width, height, ratio]
        handle?.resize(width, height, ratio)
      },
      dispose: () => {
        disposed = true
        handle?.dispose()
      },
    }

    void import('three').then((three) => {
      if (disposed) return
      const renderer = new three.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' })
      renderer.setClearColor(0x000000, 0)
      const scene = new three.Scene()
      const camera = new three.PerspectiveCamera(36, 1, 0.1, 50)
      camera.position.set(0, 0, 5.4)

      const palette = () => orbTheme(themeRef.current)
      const color = (hex: string) => new three.Color(...hexToRgb(hex))

      const coreUniforms = {
        uTime: { value: 0 },
        uAmp: { value: 0 },
        uWobble: { value: 0 },
        uDeep: { value: color(palette().core[0]) },
        uMid: { value: color(palette().core[1]) },
        uBright: { value: color(palette().core[2]) },
      }
      const core = new three.Mesh(
        new three.IcosahedronGeometry(1, 48),
        new three.ShaderMaterial({ uniforms: coreUniforms, vertexShader: CORE_VERTEX, fragmentShader: CORE_FRAGMENT }),
      )
      scene.add(core)

      // A shell of points on a Fibonacci sphere, rippling with the voice.
      const count = 2200
      const positions = new Float32Array(count * 3)
      const seeds = new Float32Array(count)
      const golden = Math.PI * (3 - Math.sqrt(5))
      for (let i = 0; i < count; i += 1) {
        const y = 1 - (i / (count - 1)) * 2
        const r = Math.sqrt(1 - y * y)
        const theta = golden * i
        positions[i * 3] = Math.cos(theta) * r * 1.42
        positions[i * 3 + 1] = y * 1.42
        positions[i * 3 + 2] = Math.sin(theta) * r * 1.42
        seeds[i] = Math.random()
      }
      const shellGeometry = new three.BufferGeometry()
      shellGeometry.setAttribute('position', new three.BufferAttribute(positions, 3))
      shellGeometry.setAttribute('aSeed', new three.BufferAttribute(seeds, 1))
      const shellUniforms = {
        uTime: { value: 0 },
        uAmp: { value: 0 },
        uPixelRatio: { value: 1 },
        uColor: { value: color(palette().particles) },
        uOpacity: { value: 0.7 },
      }
      const shell = new three.Points(
        shellGeometry,
        new three.ShaderMaterial({
          uniforms: shellUniforms,
          vertexShader: SHELL_VERTEX,
          fragmentShader: SHELL_FRAGMENT,
          transparent: true,
          depthWrite: false,
          blending: three.AdditiveBlending,
        }),
      )
      scene.add(shell)

      // Eyes: rounded capsules always drawn on top of the body.
      const eyeMaterial = new three.MeshBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true, opacity: 0.96 })
      const eyeGeometry = new three.CapsuleGeometry(0.075, 0.16, 6, 16)
      const eyes = new three.Group()
      const eyeMeshes = [-1, 1].map((side) => {
        const eye = new three.Mesh(eyeGeometry, eyeMaterial)
        eye.position.set(side * 0.21, 0.06, 0)
        eye.renderOrder = 10
        eyes.add(eye)
        return eye
      })
      eyes.position.z = 1.08
      scene.add(eyes)

      let appliedTheme = themeRef.current
      let amp = 0
      let wobble = 0
      let blinkAt = 2 + Math.random() * 3
      let blink = 0
      const eye: EyeTarget = { open: 1, scale: 1, lookX: 0, lookY: 0, squint: 0 }
      const pointer = { x: 0, y: 0 }
      const onPointer = (event: PointerEvent) => {
        pointer.x = (event.clientX / window.innerWidth) * 2 - 1
        pointer.y = (event.clientY / window.innerHeight) * 2 - 1
      }
      window.addEventListener('pointermove', onPointer, { passive: true })

      handle = {
        frame: (elapsed, delta) => {
          if (themeRef.current !== appliedTheme) {
            appliedTheme = themeRef.current
            const p = palette()
            coreUniforms.uDeep.value = color(p.core[0])
            coreUniforms.uMid.value = color(p.core[1])
            coreUniforms.uBright.value = color(p.core[2])
            shellUniforms.uColor.value = color(p.particles)
          }
          const currentPhase = phaseRef.current
          const raw = Math.max(0, Math.min(1, levelRef.current()))
          const target = currentPhase === 'muted' ? 0 : raw
          amp += (target - amp) * Math.min(1, delta * (target > amp ? 18 : 6))
          const busy = currentPhase === 'thinking' || currentPhase === 'working' || currentPhase === 'transcribing'
          wobble += ((busy ? 1 : 0) - wobble) * Math.min(1, delta * 3)

          coreUniforms.uTime.value = elapsed
          coreUniforms.uAmp.value = amp
          coreUniforms.uWobble.value = wobble
          shellUniforms.uTime.value = elapsed
          shellUniforms.uAmp.value = amp
          shellUniforms.uOpacity.value = 0.45 + amp * 0.5 + wobble * 0.15

          const breathe = 1 + Math.sin(elapsed * 1.4) * 0.012 + amp * 0.06
          core.scale.setScalar(breathe)
          core.rotation.y = elapsed * 0.12 + pointer.x * 0.25
          core.rotation.x = pointer.y * 0.15
          shell.rotation.y = elapsed * (busy ? 0.35 : 0.08)
          shell.rotation.x = Math.sin(elapsed * 0.2) * 0.2
          // The halo is CSS behind the canvas: additive sprites over a transparent canvas composite badly.
          haloRef.current?.style.setProperty('--orb-amp', amp.toFixed(3))

          // Eyes ease towards the mood of the phase and blink now and then.
          const goal = eyeTargetFor(currentPhase, elapsed, amp)
          const k = Math.min(1, delta * 10)
          eye.open += (goal.open - eye.open) * k
          eye.scale += (goal.scale - eye.scale) * k
          eye.lookX += (goal.lookX + pointer.x * 0.04 - eye.lookX) * k
          eye.lookY += (goal.lookY - pointer.y * 0.03 - eye.lookY) * k
          eye.squint += (goal.squint - eye.squint) * k
          if (elapsed >= blinkAt) {
            blink = 1
            blinkAt = elapsed + 2.5 + Math.random() * 3.5
          }
          blink = Math.max(0, blink - delta * 9)
          const blinkClose = blink > 0 ? Math.sin(blink * Math.PI) : 0
          const open = Math.max(0.08, eye.open * (1 - blinkClose * 0.92) * (1 - eye.squint * 0.35))
          for (const mesh of eyeMeshes) mesh.scale.set(eye.scale, eye.scale * open, 1)
          eyes.position.x = eye.lookX
          eyes.position.y = eye.lookY + (currentPhase === 'speaking' ? Math.sin(elapsed * 9) * amp * 0.02 : 0)

          renderer.render(scene, camera)
        },
        resize: (width, height, ratio) => {
          renderer.setPixelRatio(ratio)
          renderer.setSize(width, height, false)
          camera.aspect = width / Math.max(1, height)
          camera.updateProjectionMatrix()
          shellUniforms.uPixelRatio.value = ratio
        },
        dispose: () => {
          window.removeEventListener('pointermove', onPointer)
          core.geometry.dispose()
          ;(core.material as THREE.Material).dispose()
          shellGeometry.dispose()
          ;(shell.material as THREE.Material).dispose()
          eyeGeometry.dispose()
          eyeMaterial.dispose()
          renderer.dispose()
        },
      }
      if (pendingSize) handle.resize(...pendingSize)
    }).catch(() => {
      // Three.js failed to load: the canvas stays transparent over the CSS backdrop.
    })
    return proxy
  }, [])

  useCanvasScene(canvasRef, factory, { enabled: webgl, maxPixelRatio: 2 })

  if (!webgl) {
    return <CssOrb phase={phase} theme={theme} level={level} size={size} animate={!staticMode && canAnimateCss()} />
  }
  return (
    <div
      ref={haloRef}
      className="live-orb live-orb--webgl"
      data-phase={phase}
      style={{ '--orb-size': `${size * 1.5}px`, '--orb-glow': orbTheme(theme).glow } as CSSProperties}
    >
      <canvas ref={canvasRef} className="live-orb__canvas" aria-hidden="true" />
    </div>
  )
}

function canAnimateCss(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
