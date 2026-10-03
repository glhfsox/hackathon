from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.errors import unprocessable
from app.deps import PolicyProviderDep
from app.protocols.policy_provider import PolicyRejectedError
from app.schemas.field_errors import FieldErrors
from app.schemas.policy_document import PolicyDocument
from app.schemas.policy_saved import PolicySaved
from app.schemas.policy_validation import PolicyValidation
from app.schemas.policy_yaml_request import PolicyYamlRequest

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.get("", response_model=PolicyDocument)
async def get_policy(policy: PolicyProviderDep) -> PolicyDocument:
    snapshot = policy.current()
    return PolicyDocument(
        yaml=snapshot.yaml, version=snapshot.version, loaded_at=snapshot.loaded_at
    )


@router.post("/validate", response_model=PolicyValidation)
async def validate_policy(body: PolicyYamlRequest, policy: PolicyProviderDep) -> PolicyValidation:
    errors = policy.validate(body.yaml)
    return PolicyValidation(valid=not errors, errors=errors)


@router.put(
    "",
    response_model=PolicySaved,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": FieldErrors}},
)
async def save_policy(
    body: PolicyYamlRequest, policy: PolicyProviderDep
) -> PolicySaved | JSONResponse:
    """Validates, writes the policy file and activates it. Invalid: 422, the old policy stays."""
    try:
        snapshot = await policy.save(body.yaml)
    except PolicyRejectedError as exc:
        return unprocessable(exc.errors)
    return PolicySaved(version=snapshot.version, loaded_at=snapshot.loaded_at)
