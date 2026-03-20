"""Hot-swappable tunable settings and related tooling."""

import inspect
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.routers.deps import get_db, get_hot_config
from app.services.hot_config import TUNABLE_SETTINGS

router = APIRouter()


class TunableSettingOut(BaseModel):
    key: str
    value: Any
    default_value: Any
    type: str
    category: str
    label: str = ""
    description: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None


class BatchUpdateRequest(BaseModel):
    updates: dict[str, Any]


class GraduationStatusOut(BaseModel):
    min_paper_days_for_live: int
    min_paper_sharpe_for_live: float
    min_paper_trades_for_live: int
    min_shadow_days: int


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _hc_get(hc: Any, key: str, default: Any) -> Any:
    if hc is None:
        return default
    getter = getattr(hc, "get", None)
    if getter is None:
        return default
    return await _maybe_await(getter(key))


async def _hc_set(hc: Any, key: str, value: Any) -> None:
    if hc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hot configuration is not available",
        )
    for name in ("set", "put", "set_value", "update_tunable"):
        fn = getattr(hc, name, None)
        if callable(fn):
            await _maybe_await(fn(key, value))
            return
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="HotConfig does not support single-key updates",
    )


async def _hc_set_many(hc: Any, updates: dict[str, Any]) -> None:
    if hc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hot configuration is not available",
        )
    for name in ("set_many", "batch_update", "update_many", "apply_batch"):
        fn = getattr(hc, name, None)
        if callable(fn):
            await _maybe_await(fn(updates))
            return
    for k, v in updates.items():
        await _hc_set(hc, k, v)


async def _hc_reload(hc: Any) -> None:
    if hc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hot configuration is not available",
        )
    fn = getattr(hc, "reload", None)
    if not callable(fn):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="HotConfig does not support reload",
        )
    await _maybe_await(fn())


def _meta_for_key(key: str) -> dict[str, Any]:
    try:
        raw = TUNABLE_SETTINGS[key]
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown tunable key: {key}",
        ) from exc
    return raw if isinstance(raw, dict) else {}


@router.get("/tunable", response_model=list[TunableSettingOut])
async def list_tunables(
    hc: Annotated[Any, Depends(get_hot_config)],
) -> list[TunableSettingOut]:
    out: list[TunableSettingOut] = []
    for key, meta in TUNABLE_SETTINGS.items():
        if not isinstance(meta, dict):
            continue
        default = meta.get("default")
        current = await _hc_get(hc, key, default)
        out.append(
            TunableSettingOut(
                key=key,
                value=current,
                default_value=default,
                type=str(meta.get("type", "string")),
                category=str(meta.get("category", "general")),
                label=str(meta.get("label", key)),
                description=str(meta.get("description", "")),
                min=meta.get("min"),
                max=meta.get("max"),
                step=meta.get("step"),
            )
        )
    return out


@router.get("/tunable/categories")
async def list_tunable_categories() -> list[str]:
    cats: set[str] = set()
    for meta in TUNABLE_SETTINGS.values():
        if isinstance(meta, dict) and "category" in meta:
            cats.add(str(meta["category"]))
    return sorted(cats)


class TunablePutBody(BaseModel):
    value: Any


@router.put("/tunable/{key}", response_model=TunableSettingOut)
async def put_tunable(
    key: str,
    body: TunablePutBody,
    hc: Annotated[Any, Depends(get_hot_config)],
) -> TunableSettingOut:
    meta = _meta_for_key(key)
    await _hc_set(hc, key, body.value)
    default = meta.get("default")
    current = await _hc_get(hc, key, body.value)
    return TunableSettingOut(
        key=key,
        value=current,
        default_value=default,
        type=str(meta.get("type", "string")),
        category=str(meta.get("category", "general")),
        label=str(meta.get("label", key)),
        description=str(meta.get("description", "")),
        min=meta.get("min"),
        max=meta.get("max"),
        step=meta.get("step"),
    )


@router.put("/tunable/batch")
async def put_tunable_batch(
    body: BatchUpdateRequest,
    hc: Annotated[Any, Depends(get_hot_config)],
) -> dict[str, str]:
    for key in body.updates:
        if key not in TUNABLE_SETTINGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown tunable key: {key}",
            )
    await _hc_set_many(hc, body.updates)
    return {"status": "ok"}


@router.post("/tunable/reload")
async def reload_tunables(
    hc: Annotated[Any, Depends(get_hot_config)],
) -> dict[str, str]:
    await _hc_reload(hc)
    return {"status": "reloaded"}


@router.post("/optimize")
async def run_settings_optimizer(
    hc: Annotated[Any, Depends(get_hot_config)],
    db: Annotated[AsyncSession, Depends(get_db)],
    lookback_days: int | None = Query(default=None, ge=1, le=3650),
) -> Any:
    if hc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hot configuration is not available",
        )

    from app.services.settings_optimizer import run_optimization

    return await run_optimization(
        hot_config=hc,
        db_session=db,
        lookback_days=lookback_days or 30,
    )


@router.get("/graduation", response_model=GraduationStatusOut)
def graduation_status() -> GraduationStatusOut:
    ex = get_settings().execution
    return GraduationStatusOut(
        min_paper_days_for_live=ex.min_paper_days_for_live,
        min_paper_sharpe_for_live=ex.min_paper_sharpe_for_live,
        min_paper_trades_for_live=ex.min_paper_trades_for_live,
        min_shadow_days=ex.min_shadow_days,
    )
