from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .models import *
from .models import same_scope


class ProviderInvocationValidator:
    def __init__(self, catalog=None, *, clock=None, cancellation=None) -> None:
        self._catalog = catalog
        self._clock = clock
        self._cancellation = cancellation

    def validate(self, request: ProviderInvocationRequest) -> None:
        if self._cancellation is not None and self._cancellation.is_cancelled():
            raise ProviderContractError(ProviderErrorCategory.CANCELLED, "INVOCATION_CANCELLED_BEFORE_EFFECT")
        if not same_scope(request.context, request.selection.context) or not same_scope(request.context, request.approved_requirements.context):
            raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "INVOCATION_SCOPE_MISMATCH")
        if request.approved_requirements_ref != request.approved_requirements.approved_requirements_ref or request.selection.approved_requirements_ref != request.approved_requirements_ref:
            raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "APPROVED_SNAPSHOT_MISMATCH")
        now = self._clock() if self._clock else None
        if now is not None and (request.selection.valid_until <= now or request.approved_requirements.valid_until <= now):
            raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "SELECTION_EXPIRED")
        selected = request.selection.primary
        if selected.provider_ref not in request.approved_requirements.allowed_provider_refs or selected.model_ref not in request.approved_requirements.allowed_model_refs:
            raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "SELECTION_NOT_APPROVED")
        if request.limits.maximum_input_tokens > request.approved_requirements.maximum_input_tokens or request.limits.maximum_output_tokens > request.approved_requirements.maximum_output_tokens or request.limits.maximum_total_tokens > request.approved_requirements.maximum_total_tokens:
            raise ProviderContractError(ProviderErrorCategory.CONTEXT_LIMIT, "INVOCATION_LIMIT_EXCEEDED")
        if request.response_format is not request.approved_requirements.response_format:
            raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "RESPONSE_FORMAT_NOT_APPROVED")
        if request.response_format not in {ResponseFormat.TEXT, ResponseFormat.JSON, ResponseFormat.SCHEMA_CONSTRAINED}:
            raise ProviderContractError(ProviderErrorCategory.UNSUPPORTED_CAPABILITY, "RESPONSE_FORMAT_UNSUPPORTED")
        if self._catalog is not None:
            descriptor = self._catalog.get_model(AuthorizedModelQuery(request.context, selected.model_ref))
            provider = self._catalog.get_provider(selected.provider_ref)
            if descriptor.provider_ref != selected.provider_ref or descriptor.provider_binding_ref != selected.provider_binding_ref:
                raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "MODEL_BINDING_MISMATCH")
            if descriptor.status in {ModelStatus.DISABLED, ModelStatus.RETIRED} or provider is None or provider.status in {ProviderStatus.DISABLED, ProviderStatus.RETIRED}:
                raise ProviderContractError(ProviderErrorCategory.MODEL_UNAVAILABLE, "MODEL_NOT_ACTIVE")
        if any(isinstance(part, ImagePart) for message in request.messages for part in message.parts):
            if InputKind.IMAGE not in request.approved_requirements.input_kinds:
                raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "IMAGE_NOT_APPROVED")
            if request.limits.maximum_images < sum(
                isinstance(part, ImagePart) for message in request.messages for part in message.parts
            ):
                raise ProviderContractError(ProviderErrorCategory.UNSUPPORTED_CAPABILITY, "IMAGE_LIMIT_EXCEEDED")
        if request.tools:
            if RequiredCapability.TOOLS not in request.approved_requirements.required_capabilities:
                raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "TOOLS_NOT_APPROVED")
            if request.limits.maximum_tool_calls < len(request.tools):
                raise ProviderContractError(ProviderErrorCategory.UNSUPPORTED_CAPABILITY, "TOOL_LIMIT_EXCEEDED")


class ProviderOutcomeNormalizer:
    @staticmethod
    def error(category: ProviderErrorCategory, code: str, *, provider_ref: ProviderRef, retryability: Retryability = Retryability.NEVER, message: str = "provider operation failed") -> ProviderError:
        return ProviderError(category, PublicErrorCode(code), message, retryability, provider_ref=provider_ref)

    @staticmethod
    def accumulate(outcomes: tuple[ProviderOutcome, ...]) -> tuple[ProviderUsage, ProviderCost]:
        usage = ProviderUsage()
        cost = ProviderCost()
        for outcome in outcomes:
            usage = usage.plus(outcome.usage)
            cost = cost.plus(outcome.cost)
        return usage, cost

    @staticmethod
    def from_exception(error: BaseException, *, provider_ref: ProviderRef, request_accepted: RequestAcceptance = RequestAcceptance.UNKNOWN) -> ProviderError:
        if isinstance(error, TimeoutError):
            category, retryability = ProviderErrorCategory.TIMEOUT, Retryability.SAFE
        elif isinstance(error, PermissionError):
            category, retryability = ProviderErrorCategory.AUTHORIZATION, Retryability.NEVER
        elif isinstance(error, ValueError):
            category, retryability = ProviderErrorCategory.INVALID_REQUEST, Retryability.NEVER
        else:
            category, retryability = ProviderErrorCategory.UNKNOWN, Retryability.POLICY_DEPENDENT
        return ProviderError(category, PublicErrorCode(category.value), "provider operation failed", retryability, request_accepted=request_accepted, provider_ref=provider_ref)


__all__ = ["ProviderInvocationValidator", "ProviderOutcomeNormalizer"]
