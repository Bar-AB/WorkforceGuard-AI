import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import insert_company, insert_employee, insert_finding

TENANT_TABLES = [
    "employees",
    "users",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
    "findings",
    "proposed_corrections",
    "audit_log",
    "anomaly_labels",
]

EMPLOYEE_TIME_INDEXES = [
    ("shifts", "company_id, employee_id, starts_at"),
    ("attendance_events", "company_id, employee_id, occurred_at"),
    ("access_logs", "company_id, employee_id, occurred_at"),
    ("payroll_runs", "company_id, employee_id, period_start"),
    ("findings", "company_id, employee_id, detected_at"),
]


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_tenant_table_company_id_is_not_nullable(
    rollback_conn: AsyncConnection, table: str
) -> None:
    result = await rollback_conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = 'company_id'"
        ),
        {"table": table},
    )

    assert result.scalar_one() == "NO"


@pytest.mark.parametrize(("table", "columns"), EMPLOYEE_TIME_INDEXES)
async def test_employee_time_table_has_tenant_employee_time_index(
    rollback_conn: AsyncConnection, table: str, columns: str
) -> None:
    result = await rollback_conn.execute(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = :table"),
        {"table": table},
    )

    indexdefs: list[str] = list(result.scalars())

    assert any(f"({columns})" in indexdef for indexdef in indexdefs)


async def test_shift_ending_before_start_is_rejected(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)

    with pytest.raises(IntegrityError, match="ck_shifts_ends_after_start"):
        await rollback_conn.execute(
            text(
                "INSERT INTO shifts (company_id, employee_id, starts_at, ends_at) "
                "VALUES (:c, :e, '2026-01-01 10:00+00', '2026-01-01 09:00+00')"
            ),
            {"c": company_id, "e": employee_id},
        )


async def test_payroll_with_negative_hours_is_rejected(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)

    with pytest.raises(IntegrityError, match="ck_payroll_runs_hours_non_negative"):
        await rollback_conn.execute(
            text(
                "INSERT INTO payroll_runs (company_id, employee_id, period_start, period_end, "
                "regular_hours, overtime_hours, gross_pay) "
                "VALUES (:c, :e, '2026-01-01', '2026-01-31', -1, 0, 100)"
            ),
            {"c": company_id, "e": employee_id},
        )


async def test_attendance_with_unknown_event_type_is_rejected(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)

    with pytest.raises(IntegrityError, match="ck_attendance_events_event_type"):
        await rollback_conn.execute(
            text(
                "INSERT INTO attendance_events (company_id, employee_id, event_type, occurred_at) "
                "VALUES (:c, :e, 'teleport', '2026-01-01 09:00+00')"
            ),
            {"c": company_id, "e": employee_id},
        )


async def test_row_pointing_at_other_company_employee_is_rejected(
    rollback_conn: AsyncConnection,
) -> None:
    company_a = await insert_company(rollback_conn)
    company_b = await insert_company(rollback_conn)
    employee_of_b = await insert_employee(rollback_conn, company_b)

    with pytest.raises(IntegrityError, match="fk_shifts_employee"):
        await rollback_conn.execute(
            text(
                "INSERT INTO shifts (company_id, employee_id, starts_at, ends_at) "
                "VALUES (:c, :e, '2026-01-01 09:00+00', '2026-01-01 17:00+00')"
            ),
            {"c": company_a, "e": employee_of_b},
        )


async def test_decided_correction_without_approver_is_rejected(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)

    with pytest.raises(IntegrityError, match="ck_proposed_corrections_decision_matches_status"):
        await rollback_conn.execute(
            text(
                "INSERT INTO proposed_corrections (company_id, finding_id, target_table, "
                "target_id, patch, reason, proposed_by, status, decided_at) "
                "VALUES (:c, :f, 'shifts', gen_random_uuid(), '{}', 'r', 'agent', "
                "'approved', now())"
            ),
            {"c": company_id, "f": finding_id},
        )


async def test_finding_with_same_dedup_key_in_same_company_is_rejected(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    await insert_finding(rollback_conn, company_id, employee_id, dedup_key="k")

    with pytest.raises(IntegrityError, match="uq_findings_company_id_dedup_key"):
        await insert_finding(rollback_conn, company_id, employee_id, dedup_key="k")


async def test_finding_with_same_dedup_key_in_other_company_is_allowed(
    rollback_conn: AsyncConnection,
) -> None:
    for _ in range(2):
        company_id = await insert_company(rollback_conn)
        employee_id = await insert_employee(rollback_conn, company_id)
        await insert_finding(rollback_conn, company_id, employee_id, dedup_key="k")
