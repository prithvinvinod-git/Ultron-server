"""Usage repository (T015).

The aggregates here are deliberately single-query. Splitting a total into a
"count these" query and a "sum those" query invites the two to disagree, because
a row can arrive between them -- and a usage dashboard that reports more calls
than tokens, or a cost total that does not match its own breakdown, is the kind
of discrepancy nobody trusts afterwards.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import case, func, select

from app.core.errors import InvalidInputError
from app.database.models import ModelUsage, ModelUsageStatus
from app.database.repositories.base import UuidRepository, apply_limit, scalars


class ModelUsageRepository(UuidRepository[ModelUsage]):
    """Per-call model usage, for cost and reliability tracking."""

    model = ModelUsage

    async def record(
        self,
        *,
        model: str,
        status: ModelUsageStatus = ModelUsageStatus.SUCCESS,
        provider: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        task_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        error: str | None = None,
        recorded_at: datetime | None = None,
    ) -> ModelUsage:
        """Record one model call.

        Negative counts and negative costs are refused rather than stored. A
        negative figure would sum into a total that looks like a discount, and
        these aggregates are the entire point of the table.
        """
        counts = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        for name, value in counts.items():
            if value is not None and value < 0:
                raise InvalidInputError(
                    f"{name} cannot be negative",
                    details={name: value},
                )
        if cost_usd is not None and cost_usd < 0:
            raise InvalidInputError(
                "cost cannot be negative",
                details={"cost_usd": str(cost_usd)},
            )
        if latency_ms is not None and latency_ms < 0:
            raise InvalidInputError(
                "latency cannot be negative",
                details={"latency_ms": latency_ms},
            )

        usage = ModelUsage(
            model=model,
            provider=provider,
            status=status,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            request_id=request_id,
            correlation_id=correlation_id,
            task_id=task_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            error=error[:4000] if error else None,
            recorded_at=recorded_at or datetime.now(UTC),
        )
        return await self.add(usage)

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[ModelUsage]:
        """Return a task's model calls, oldest first."""
        statement = (
            select(ModelUsage).where(ModelUsage.task_id == task_id).order_by(ModelUsage.recorded_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def totals_for_task(self, task_id: uuid.UUID) -> dict[str, Decimal | int]:
        """Return summed tokens, cost, and call count for a task.

        One query for every figure, so the cost and the token count always come
        from the same set of rows.
        """
        statement = select(
            func.coalesce(func.sum(ModelUsage.prompt_tokens), 0),
            func.coalesce(func.sum(ModelUsage.completion_tokens), 0),
            func.coalesce(func.sum(ModelUsage.cost_usd), Decimal(0)),
            func.count(),
        ).where(ModelUsage.task_id == task_id)
        result = await self._session.execute(statement)
        prompt, completion, cost, calls = result.one()
        return {
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "cost_usd": Decimal(cost or 0),
            "calls": int(calls or 0),
        }

    async def totals_by_model(
        self,
        *,
        since: datetime,
        limit: int | None = None,
    ) -> list[tuple[str, int, Decimal]]:
        """Return ``(model, calls, cost)`` per model since a time, costliest first.

        ``since`` is required. This is the query that drives spend reporting, and
        an unbounded version of it over a table that only grows is both slow and a
        good way for a dashboard to expose someone's whole history by accident.
        """
        statement = (
            select(
                ModelUsage.model,
                func.count(),
                func.coalesce(func.sum(ModelUsage.cost_usd), Decimal(0)),
            )
            .where(ModelUsage.recorded_at >= since)
            .group_by(ModelUsage.model)
            .order_by(func.coalesce(func.sum(ModelUsage.cost_usd), Decimal(0)).desc())
        )
        result = await self._session.execute(apply_limit(statement, limit))
        return [(str(model), int(calls), Decimal(cost or 0)) for model, calls, cost in result.all()]

    async def failure_rate(self, model: str, *, since: datetime) -> Decimal:
        """Return the fraction of calls to ``model`` that failed, as 0..1.

        ``case()`` rather than a boolean cast on the comparison, because a bare
        ``cast(int)`` is not portable and this runs on SQLite in the unit tests
        and PostgreSQL in production. One query for numerator and denominator, so
        the rate cannot be computed from two different sets of rows.
        """
        statement = select(
            func.count(),
            func.coalesce(
                func.sum(case((ModelUsage.status != ModelUsageStatus.SUCCESS, 1), else_=0)),
                0,
            ),
        ).where(ModelUsage.model == model, ModelUsage.recorded_at >= since)
        result = await self._session.execute(statement)
        total, failures = result.one()
        return Decimal(failures or 0) / Decimal(total) if total else Decimal(0)

    async def latency_percentile(
        self,
        model: str,
        *,
        since: datetime,
        percentile: float = 0.95,
    ) -> int | None:
        """Return a latency percentile in milliseconds for one model.

        ``percentile`` must be in 0..1. A value outside that range would be
        silently clamped by the database's own interpretation, so the caller
        would get a number back that does not correspond to what it asked for.

        Nearest-rank, not ``int(n * p)``. Truncating the rank is off by one
        whenever the product lands on a whole number: for 100 samples at p95,
        ``int(100 * 0.95)`` is 95, which addresses the *96th* smallest value and
        reports 96 where the 95th percentile is 95. Rounding the rank up and
        converting to a zero-based index makes the 95th percentile of 1..100 come
        out at 95.

        The sample is ordered before it is capped, so the 1000 rows used are the
        oldest 1000 rather than whatever the database happened to return. A
        percentile over an arbitrary subset of a large table is not a percentile
        of the table, and leaving the order to the planner makes the reported
        figure change as the table grows.
        """
        if not 0.0 < percentile < 1.0:
            raise InvalidInputError(
                "percentile must be between 0 and 1",
                details={"percentile": percentile},
            )
        statement = (
            select(ModelUsage.latency_ms)
            .where(
                ModelUsage.model == model,
                ModelUsage.recorded_at >= since,
                ModelUsage.latency_ms.is_not(None),
            )
            .order_by(ModelUsage.recorded_at)
        )
        result = await self._session.execute(apply_limit(statement, 1000))
        samples: list[int] = scalars(result)
        if not samples:
            return None
        ordered = sorted(samples)
        rank = min(len(ordered), max(1, math.ceil(len(ordered) * percentile)))
        return ordered[rank - 1]

    async def list_errors(self, *, limit: int | None = None) -> list[ModelUsage]:
        """Return failed calls, newest first."""
        statement = (
            select(ModelUsage)
            .where(ModelUsage.status == ModelUsageStatus.ERROR)
            .order_by(ModelUsage.recorded_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def total_cost_since(self, since: datetime) -> Decimal:
        """Return total spend since a time.

        Separate from :meth:`totals_by_model` because the global total is what a
        budget alert watches, and it should not require grouping every row by
        model to get one number.
        """
        statement = select(func.coalesce(func.sum(ModelUsage.cost_usd), Decimal(0))).where(
            ModelUsage.recorded_at >= since
        )
        result = await self._session.execute(statement)
        return Decimal(result.scalar_one() or 0)


__all__ = ["ModelUsageRepository"]
