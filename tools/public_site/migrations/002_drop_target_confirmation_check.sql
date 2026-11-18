-- Recording no longer requires an operator-confirmed player name.
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'review_code_acquisitions'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%target_confirmed_at%';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE review_code_acquisitions DROP CONSTRAINT %I',
            constraint_name
        );
    END IF;
END $$;
