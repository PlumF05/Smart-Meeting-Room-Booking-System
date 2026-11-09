"""预约表真实MySQL验证；只访问fixture新建的独立实例，不读取开发凭据。"""

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import secrets

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.reservation_enums import ReservationSource, ReservationStatus
from app.models import LoginSession, MeetingRoom, Reservation, User
from app.services.booking_time import BEIJING, to_utc_naive, utc_naive_to_beijing


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例，不能在开发库验证迁移回退",
    ),
]
BACKEND = Path(__file__).resolve().parents[1]
START = datetime(2026, 11, 8, 0, 0, 0, 123456)


def migration_config(connection):
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def unique(prefix):
    return prefix + "_" + secrets.token_hex(5)


def core_snapshots(engine):
    with engine.connect() as connection:
        return {
            model.__tablename__: list(connection.execute(select(model.__table__).order_by(model.id)).all())
            for model in (User, MeetingRoom, LoginSession)
        }


def test_0003_upgrade_and_downgrade_preserve_existing_user_room_and_login_data(engine):
    # 回退仅发生在本测试模块专有实例中；0003只允许删除自己的预约表。
    with engine.connect() as connection:
        command.downgrade(migration_config(connection), "0002")
    with Session(engine) as database, database.begin():
        user = User(username=unique("preserved"), password_hash="synthetic-no-login-hash",
                    full_name="保留数据测试学生", identity_type="student")
        room = MeetingRoom(name=unique("保留资料房间"), location="测试楼", capacity=12, equipment=["白板"])
        database.add_all([user, room])
        database.flush()
        database.add(LoginSession(
            token_hash=secrets.token_hex(32), user_id=user.id, csrf_token="x" * 43,
            session_version=1, expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        ))
    before = core_snapshots(engine)
    with engine.connect() as connection:
        command.upgrade(migration_config(connection), "0003")
        assert MigrationContext.configure(connection).get_current_revision() == "0003"
        assert "reservations" in inspect(connection).get_table_names()
    assert core_snapshots(engine) == before
    with engine.connect() as connection:
        config = migration_config(connection)
        command.downgrade(config, "0002")
        assert "reservations" not in inspect(connection).get_table_names()
        connection.rollback()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        connection.rollback()
        command.check(config)
    assert core_snapshots(engine) == before


@pytest.fixture
def references(engine):
    with Session(engine) as database, database.begin():
        user = User(username=unique("reservation"), password_hash="synthetic-no-login-hash",
                    full_name="预约结构测试学生", identity_type="student")
        room = MeetingRoom(name=unique("快照测试会议室"), location="测试楼201", capacity=12,
                           equipment=["投影仪", "白板"])
        database.add_all([user, room])
        database.flush()
        return {"user_id": user.id, "room_id": room.id, "name": room.name}


def reservation_values(references, **changes):
    values = dict(
        user_id=references["user_id"], room_id=references["room_id"],
        title="独立实例中的预约结构测试", purpose="检查保存与数据库约束", attendees=6,
        starts_at=START, ends_at=START + timedelta(hours=1), required_equipment=["白板"],
        room_name_snapshot=references["name"], room_location_snapshot="测试楼201",
        room_capacity_snapshot=12, room_equipment_snapshot=["投影仪", "白板"],
    )
    values.update(changes)
    return values


def insert_reservation(engine, values):
    with Session(engine) as database, database.begin():
        row = Reservation(**values)
        database.add(row)
        database.flush()
        return row.id


def test_committed_reservation_persists_in_another_session_with_precise_utc_time(engine, references):
    local_start = datetime(2026, 11, 8, 8, 0, 0, 123456, tzinfo=BEIJING)
    reservation_id = insert_reservation(engine, reservation_values(
        references, starts_at=to_utc_naive(local_start),
        ends_at=to_utc_naive(local_start + timedelta(hours=1)),
    ))
    with Session(engine) as database:
        row = database.get(Reservation, reservation_id)
        assert row.starts_at == START and row.starts_at.tzinfo is None
        assert utc_naive_to_beijing(row.starts_at) == local_start
        assert row.status == ReservationStatus.PENDING and row.source == ReservationSource.PAGE
        assert row.status_reason is None
        assert row.required_equipment == ["白板"] and row.room_equipment_snapshot == ["投影仪", "白板"]
        assert row.submitted_at.tzinfo is None and row.updated_at.tzinfo is None


def test_exception_rolls_back_new_reservation(engine, references):
    with pytest.raises(RuntimeError, match="intentional rollback"):
        with Session(engine) as database, database.begin():
            row = Reservation(**reservation_values(references))
            database.add(row)
            database.flush()
            reservation_id = row.id
            raise RuntimeError("intentional rollback")
    with Session(engine) as database:
        assert database.get(Reservation, reservation_id) is None


def test_room_rename_and_equipment_change_do_not_rewrite_reservation_snapshot(engine, references):
    reservation_id = insert_reservation(engine, reservation_values(references))
    with Session(engine) as database, database.begin():
        room = database.get(MeetingRoom, references["room_id"])
        room.name = unique("改名后的房间")
        room.equipment = ["视频会议"]
    with Session(engine) as database:
        row = database.get(Reservation, reservation_id)
        room = database.get(MeetingRoom, row.room_id)
        assert row.room_id == references["room_id"]
        assert row.room_name_snapshot == references["name"] and room.name != row.room_name_snapshot
        assert row.room_equipment_snapshot == ["投影仪", "白板"] and room.equipment == ["视频会议"]
        assert row.room_location_snapshot == "测试楼201" and row.room_capacity_snapshot == 12


@pytest.mark.parametrize("changes", [
    {"attendees": 0}, {"attendees": -1}, {"attendees": 13},
    {"room_capacity_snapshot": 0}, {"room_capacity_snapshot": -1},
    {"ends_at": START}, {"ends_at": START - timedelta(minutes=30)},
    {"required_equipment": {}}, {"required_equipment": "白板"}, {"required_equipment": None},
    {"room_equipment_snapshot": {}}, {"room_equipment_snapshot": "投影仪"},
    {"status": "unknown"}, {"status": "PENDING"}, {"status": "pénding"},
    {"source": "web"}, {"source": "PAGE"}, {"source": "ássistant"},
])
def test_database_static_checks_reject_invalid_rows(engine, references, changes):
    with pytest.raises(DBAPIError) as error:
        insert_reservation(engine, reservation_values(references, **changes))
    assert error.value.orig.args[0] == 3819


@pytest.mark.parametrize("status", list(ReservationStatus))
def test_all_declared_status_values_can_be_stored(engine, references, status):
    reservation_id = insert_reservation(engine, reservation_values(references, status=status))
    with Session(engine) as database:
        assert database.get(Reservation, reservation_id).status == status.value


@pytest.mark.parametrize("source", list(ReservationSource))
def test_both_declared_entry_sources_can_be_stored(engine, references, source):
    reservation_id = insert_reservation(engine, reservation_values(references, source=source))
    with Session(engine) as database:
        assert database.get(Reservation, reservation_id).source == source.value


@pytest.mark.parametrize("column", ["user_id", "room_id"])
def test_nonexistent_foreign_key_is_rejected(engine, references, column):
    with pytest.raises(DBAPIError) as error:
        insert_reservation(engine, reservation_values(references, **{column: 9223372036854775807}))
    assert error.value.orig.args[0] == 1452


@pytest.mark.parametrize("model, key", [(User, "user_id"), (MeetingRoom, "room_id")])
def test_referenced_parent_cannot_be_deleted_and_reservation_remains(engine, references, model, key):
    reservation_id = insert_reservation(engine, reservation_values(references))
    with pytest.raises(DBAPIError) as error:
        with Session(engine) as database, database.begin():
            database.execute(delete(model).where(model.id == references[key]))
    assert error.value.orig.args[0] == 1451
    with Session(engine) as database:
        assert database.get(model, references[key]) is not None
        assert database.get(Reservation, reservation_id) is not None


def test_raw_insert_defaults_use_utc_independent_of_connection_timezone(engine, references):
    with engine.connect() as connection:
        original_zone = connection.scalar(text("SELECT @@session.time_zone"))
        try:
            connection.execute(text("SET SESSION time_zone='+08:00'"))
            result = connection.execute(text(
                "INSERT INTO reservations "
                "(user_id,room_id,title,purpose,attendees,starts_at,ends_at,room_name_snapshot,"
                "room_location_snapshot,room_capacity_snapshot,room_equipment_snapshot) "
                "VALUES (:uid,:rid,'默认时间测试','检查UTC默认值',1,:starts,:ends,'快照房间',"
                "'测试楼',12,JSON_ARRAY('白板'))"
            ), {"uid": references["user_id"], "rid": references["room_id"],
                "starts": START, "ends": START + timedelta(hours=1)})
            row = connection.execute(select(Reservation.__table__).where(Reservation.id == result.lastrowid)).one()
            utc_now = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
            assert row.status == "pending" and row.source == "page" and row.required_equipment == []
            for saved in (row.submitted_at, row.updated_at):
                assert saved.tzinfo is None and 0 <= (utc_now - saved).total_seconds() < 5
        finally:
            connection.rollback()
            connection.execute(text("SET SESSION time_zone=:zone"), {"zone": original_zone})
            connection.rollback()


def test_orm_update_refreshes_updated_at_without_a_database_trigger(engine, references):
    old_time = datetime(2000, 1, 1)
    reservation_id = insert_reservation(engine, reservation_values(references, updated_at=old_time))
    with Session(engine) as database, database.begin():
        row = database.get(Reservation, reservation_id)
        row.title = "ORM更新后的主题"
    with Session(engine) as database:
        saved = database.get(Reservation, reservation_id).updated_at
        assert saved != old_time and saved.tzinfo is None
    # 直接SQL没有SQLAlchemy的onupdate行为，不能误称数据库会自动补此时间。
    with engine.begin() as connection:
        connection.execute(text("UPDATE reservations SET title='直接SQL更新' WHERE id=:id"), {"id": reservation_id})
    with Session(engine) as database:
        assert database.get(Reservation, reservation_id).updated_at == saved


def test_conflict_query_indexes_are_nonunique_and_match_the_two_lookup_paths(engine):
    indexes = {tuple(item["column_names"]): item for item in inspect(engine).get_indexes("reservations")}
    for columns in (
        ("room_id", "status", "starts_at", "ends_at"),
        ("user_id", "status", "starts_at", "ends_at"),
        ("submitted_at",),
    ):
        assert columns in indexes and indexes[columns]["unique"] is False
    assert not inspect(engine).get_unique_constraints("reservations")
