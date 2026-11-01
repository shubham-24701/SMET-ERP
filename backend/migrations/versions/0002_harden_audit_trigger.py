"""harden the audit trigger and protect the migration record

The owner check now reads the trigger's own table from pg_catalog (TG_RELID) with a fixed
search_path, so a temporary view named pg_tables can't change its answer. The app login loses
write access to alembic_version, so it can't make the database claim another schema version.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OWNER_CHECK = """
    current_setting('smet.audit_maintenance', true) = 'on'
    AND current_user = (SELECT pg_get_userbyid(relowner) FROM pg_catalog.pg_class WHERE oid = TG_RELID)
"""


def upgrade() -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF {OWNER_CHECK} THEN RETURN NULL; END IF;
            RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'insufficient_privilege';
        END $$;
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION audit_events_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF {OWNER_CHECK} THEN RETURN COALESCE(OLD, NEW); END IF;
            RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'insufficient_privilege';
        END $$;
    """)
    op.execute("""
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
                     WHERE table_name = 'alembic_version' AND grantee <> current_user
                       AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') LOOP
                EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM %I', r.grantee);
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    pass  # the 0001 functions were weaker; keeping the stronger ones is safe
