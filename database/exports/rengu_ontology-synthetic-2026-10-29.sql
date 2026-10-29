--
-- PostgreSQL database dump
--

\restrict njo8d1fnYkstDKeDaufeewylqvDax1NmQNiQSB6lbedM2BAguT38SLPCrSPfHTD

-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: procrastinate_job_event_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procrastinate_job_event_type AS ENUM (
    'deferred',
    'started',
    'deferred_for_retry',
    'failed',
    'succeeded',
    'cancelled',
    'abort_requested',
    'aborted',
    'scheduled',
    'retried'
);


--
-- Name: procrastinate_job_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procrastinate_job_status AS ENUM (
    'todo',
    'doing',
    'succeeded',
    'failed',
    'cancelled',
    'aborting',
    'aborted'
);


--
-- Name: procrastinate_job_to_defer_v1; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procrastinate_job_to_defer_v1 AS (
	queue_name character varying,
	task_name character varying,
	priority integer,
	lock text,
	queueing_lock text,
	args jsonb,
	scheduled_at timestamp with time zone
);


--
-- Name: append_only_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.append_only_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION '% 只追加，不允许 %', TG_TABLE_NAME, TG_OP USING ERRCODE = 'raise_exception';
    END $$;


--
-- Name: ontology_reference_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ontology_reference_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
        DECLARE referenced_id bigint; parent_ontology bigint;
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.ontology_id IS DISTINCT FROM OLD.ontology_id THEN
                RAISE EXCEPTION 'Moving records between ontology workspaces is forbidden'
                    USING ERRCODE='23514';
            END IF;
            referenced_id := (to_jsonb(NEW)->>TG_ARGV[0])::bigint;
            IF referenced_id IS NOT NULL THEN
                EXECUTE format('SELECT ontology_id FROM %I WHERE id=$1',TG_ARGV[1])
                    INTO parent_ontology USING referenced_id;
                IF parent_ontology IS NULL OR parent_ontology <> NEW.ontology_id THEN
                    RAISE EXCEPTION 'Ontology reference is unavailable in this workspace'
                        USING ERRCODE='23503';
                END IF;
            END IF;
            RETURN NEW;
        END $_$;


--
-- Name: procrastinate_cancel_job_v1(bigint, boolean, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_cancel_job_v1(job_id bigint, abort boolean, delete_job boolean) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status = 'todo'
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        IF abort THEN
            UPDATE procrastinate_jobs
            SET abort_requested = true,
                status = CASE status
                    WHEN 'todo' THEN 'cancelled'::procrastinate_job_status ELSE status
                END
            WHERE id = job_id AND status IN ('todo', 'doing')
            RETURNING id INTO _job_id;
        ELSE
            UPDATE procrastinate_jobs
            SET status = 'cancelled'::procrastinate_job_status
            WHERE id = job_id AND status = 'todo'
            RETURNING id INTO _job_id;
        END IF;
    END IF;
    RETURN _job_id;
END;
$$;


--
-- Name: procrastinate_defer_jobs_v1(public.procrastinate_job_to_defer_v1[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_defer_jobs_v1(jobs public.procrastinate_job_to_defer_v1[]) RETURNS bigint[]
    LANGUAGE plpgsql
    AS $$
DECLARE
    job_ids bigint[];
BEGIN
    WITH inserted_jobs AS (
        INSERT INTO procrastinate_jobs (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at)
        SELECT (job).queue_name,
               (job).task_name,
               (job).priority,
               (job).lock,
               (job).queueing_lock,
               (job).args,
               (job).scheduled_at
        FROM unnest(jobs) AS job
        RETURNING id
    )
    SELECT array_agg(id) FROM inserted_jobs INTO job_ids;

    RETURN job_ids;
END;
$$;


--
-- Name: procrastinate_defer_periodic_job_v2(character varying, character varying, character varying, character varying, integer, character varying, bigint, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_defer_periodic_job_v2(_queue_name character varying, _lock character varying, _queueing_lock character varying, _task_name character varying, _priority integer, _periodic_id character varying, _defer_timestamp bigint, _args jsonb) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
	_job_id bigint;
	_defer_id bigint;
BEGIN
    INSERT
        INTO procrastinate_periodic_defers (task_name, periodic_id, defer_timestamp)
        VALUES (_task_name, _periodic_id, _defer_timestamp)
        ON CONFLICT DO NOTHING
        RETURNING id into _defer_id;

    IF _defer_id IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE procrastinate_periodic_defers
        SET job_id = (
            SELECT COALESCE((
                SELECT unnest(procrastinate_defer_jobs_v1(
                    ARRAY[
                        ROW(
                            _queue_name,
                            _task_name,
                            _priority,
                            _lock,
                            _queueing_lock,
                            _args,
                            NULL::timestamptz
                        )
                    ]::procrastinate_job_to_defer_v1[]
                ))
            ), NULL)
        )
        WHERE id = _defer_id
        RETURNING job_id INTO _job_id;

    DELETE
        FROM procrastinate_periodic_defers
        USING (
            SELECT id
            FROM procrastinate_periodic_defers
            WHERE procrastinate_periodic_defers.task_name = _task_name
            AND procrastinate_periodic_defers.periodic_id = _periodic_id
            AND procrastinate_periodic_defers.defer_timestamp < _defer_timestamp
            ORDER BY id
            FOR UPDATE
        ) to_delete
        WHERE procrastinate_periodic_defers.id = to_delete.id;

    RETURN _job_id;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: procrastinate_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_jobs (
    id bigint NOT NULL,
    queue_name character varying(128) NOT NULL,
    task_name character varying(128) NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    lock text,
    queueing_lock text,
    args jsonb DEFAULT '{}'::jsonb NOT NULL,
    status public.procrastinate_job_status DEFAULT 'todo'::public.procrastinate_job_status NOT NULL,
    scheduled_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    abort_requested boolean DEFAULT false NOT NULL,
    worker_id bigint,
    CONSTRAINT check_not_todo_abort_requested CHECK ((NOT ((status = 'todo'::public.procrastinate_job_status) AND (abort_requested = true))))
);


--
-- Name: procrastinate_fetch_job_v2(character varying[], bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_fetch_job_v2(target_queue_names character varying[], p_worker_id bigint) RETURNS public.procrastinate_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
	found_jobs procrastinate_jobs;
BEGIN
    WITH candidate AS (
        SELECT jobs.*
            FROM procrastinate_jobs AS jobs
            WHERE
                -- reject the job if its lock has earlier or higher priority jobs
                NOT EXISTS (
                    SELECT 1
                        FROM procrastinate_jobs AS other_jobs
                        WHERE
                            jobs.lock IS NOT NULL
                            AND other_jobs.lock = jobs.lock
                            AND (
                                -- job with same lock is already running
                                other_jobs.status = 'doing'
                                OR
                                -- job with same lock is waiting and has higher priority (or same priority but was queued first)
                                (
                                    other_jobs.status = 'todo'
                                    AND (
                                        other_jobs.priority > jobs.priority
                                        OR (
                                        other_jobs.priority = jobs.priority
                                        AND other_jobs.id < jobs.id
                                        )
                                    )
                                )
                            )
                )
                AND jobs.status = 'todo'
                AND (target_queue_names IS NULL OR jobs.queue_name = ANY( target_queue_names ))
                AND (jobs.scheduled_at IS NULL OR jobs.scheduled_at <= now())
            ORDER BY jobs.priority DESC, jobs.id ASC LIMIT 1
            FOR UPDATE OF jobs SKIP LOCKED
    )
    UPDATE procrastinate_jobs
        SET status = 'doing', worker_id = p_worker_id
        FROM candidate
        WHERE procrastinate_jobs.id = candidate.id
        RETURNING procrastinate_jobs.* INTO found_jobs;

 RETURN found_jobs;
END;
$$;


--
-- Name: procrastinate_finish_job_v1(bigint, public.procrastinate_job_status, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_finish_job_v1(job_id bigint, end_status public.procrastinate_job_status, delete_job boolean) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF end_status NOT IN ('succeeded', 'failed', 'aborted') THEN
        RAISE 'End status should be either "succeeded", "failed" or "aborted" (job id: %)', job_id;
    END IF;
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status IN ('todo', 'doing')
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = end_status,
            abort_requested = false,
            attempts = CASE status
                WHEN 'doing' THEN attempts + 1 ELSE attempts
            END
        WHERE id = job_id AND status IN ('todo', 'doing')
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" or "todo" status (job id: %)', job_id;
    END IF;
END;
$$;


--
-- Name: procrastinate_notify_queue_abort_job_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_notify_queue_abort_job_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    payload TEXT;
BEGIN
    SELECT json_build_object('type', 'abort_job_requested', 'job_id', NEW.id)::text INTO payload;
	PERFORM pg_notify('procrastinate_queue_v1#' || NEW.queue_name, payload);
	PERFORM pg_notify('procrastinate_any_queue_v1', payload);
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_notify_queue_job_inserted_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_notify_queue_job_inserted_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    payload TEXT;
BEGIN
    SELECT json_build_object('type', 'job_inserted', 'job_id', NEW.id)::text INTO payload;
	PERFORM pg_notify('procrastinate_queue_v1#' || NEW.queue_name, payload);
	PERFORM pg_notify('procrastinate_any_queue_v1', payload);
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_prune_stalled_workers_v1(double precision); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_prune_stalled_workers_v1(seconds_since_heartbeat double precision) RETURNS TABLE(worker_id bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    DELETE FROM procrastinate_workers
    WHERE last_heartbeat < NOW() - (seconds_since_heartbeat || 'SECOND')::INTERVAL
    RETURNING procrastinate_workers.id;
END;
$$;


--
-- Name: procrastinate_register_worker_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_register_worker_v1() RETURNS TABLE(worker_id bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    INSERT INTO procrastinate_workers DEFAULT VALUES
    RETURNING procrastinate_workers.id;
END;
$$;


--
-- Name: procrastinate_retry_job_v1(bigint, timestamp with time zone, integer, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_retry_job_v1(job_id bigint, retry_at timestamp with time zone, new_priority integer, new_queue_name character varying, new_lock character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
    _abort_requested boolean;
BEGIN
    SELECT abort_requested FROM procrastinate_jobs
    WHERE id = job_id AND status = 'doing'
    FOR UPDATE
    INTO _abort_requested;
    IF _abort_requested THEN
        UPDATE procrastinate_jobs
        SET status = 'failed'::procrastinate_job_status
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = 'todo'::procrastinate_job_status,
            attempts = attempts + 1,
            scheduled_at = retry_at,
            priority = COALESCE(new_priority, priority),
            queue_name = COALESCE(new_queue_name, queue_name),
            lock = COALESCE(new_lock, lock)
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    END IF;

    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" status (job id: %)', job_id;
    END IF;
END;
$$;


--
-- Name: procrastinate_retry_job_v2(bigint, timestamp with time zone, integer, character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_retry_job_v2(job_id bigint, retry_at timestamp with time zone, new_priority integer, new_queue_name character varying, new_lock character varying) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    _job_id bigint;
    _abort_requested boolean;
    _current_status procrastinate_job_status;
BEGIN
    SELECT status, abort_requested FROM procrastinate_jobs
    WHERE id = job_id AND status IN ('doing', 'failed')
    FOR UPDATE
    INTO _current_status, _abort_requested;
    IF _current_status = 'doing' AND _abort_requested THEN
        UPDATE procrastinate_jobs
        SET status = 'failed'::procrastinate_job_status
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = 'todo'::procrastinate_job_status,
            attempts = attempts + 1,
            scheduled_at = retry_at,
            priority = COALESCE(new_priority, priority),
            queue_name = COALESCE(new_queue_name, queue_name),
            lock = COALESCE(new_lock, lock)
        WHERE id = job_id AND status IN ('doing', 'failed')
        RETURNING id INTO _job_id;
    END IF;

    IF _job_id IS NULL THEN
        RAISE 'Job was not found or has an invalid status to retry (job id: %)', job_id;
    END IF;

END;
$$;


--
-- Name: procrastinate_trigger_abort_requested_events_procedure_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_abort_requested_events_procedure_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'abort_requested'::procrastinate_job_event_type);
    RETURN NEW;
END;
$$;


--
-- Name: procrastinate_trigger_function_scheduled_events_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_function_scheduled_events_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type, at)
        VALUES (NEW.id, 'scheduled'::procrastinate_job_event_type, NEW.scheduled_at);

	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_trigger_function_status_events_insert_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_function_status_events_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'deferred'::procrastinate_job_event_type);
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_trigger_function_status_events_update_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_trigger_function_status_events_update_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    WITH t AS (
        SELECT CASE
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND NEW.status = 'doing'::procrastinate_job_status
                THEN 'started'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'deferred_for_retry'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'failed'::procrastinate_job_status
                THEN 'failed'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'succeeded'::procrastinate_job_status
                THEN 'succeeded'::procrastinate_job_event_type
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND (
                    NEW.status = 'cancelled'::procrastinate_job_status
                    OR NEW.status = 'failed'::procrastinate_job_status
                    OR NEW.status = 'succeeded'::procrastinate_job_status
                )
                THEN 'cancelled'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'aborted'::procrastinate_job_status
                THEN 'aborted'::procrastinate_job_event_type
            WHEN OLD.status = 'failed'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'retried'::procrastinate_job_event_type
            ELSE NULL
        END as event_type
    )
    INSERT INTO procrastinate_events(job_id, type)
        SELECT NEW.id, t.event_type
        FROM t
        WHERE t.event_type IS NOT NULL;
	RETURN NEW;
END;
$$;


--
-- Name: procrastinate_unlink_periodic_defers_v1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_unlink_periodic_defers_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE procrastinate_periodic_defers
    SET job_id = NULL
    WHERE job_id = OLD.id;
    RETURN OLD;
END;
$$;


--
-- Name: procrastinate_unregister_worker_v1(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_unregister_worker_v1(worker_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM procrastinate_workers
    WHERE id = worker_id;
END;
$$;


--
-- Name: procrastinate_update_heartbeat_v1(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.procrastinate_update_heartbeat_v1(worker_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE procrastinate_workers
    SET last_heartbeat = NOW()
    WHERE id = worker_id;
END;
$$;


--
-- Name: action_param; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_param (
    action_type_id bigint NOT NULL,
    seq integer NOT NULL,
    name character varying(64) NOT NULL,
    api character varying(64) NOT NULL,
    type character varying(64) NOT NULL,
    required boolean NOT NULL,
    default_text text NOT NULL,
    rule_text text NOT NULL,
    rule_expr jsonb,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.action_param FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_param; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_param IS '动作参数';


--
-- Name: COLUMN action_param.default_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_param.default_text IS '接口 def';


--
-- Name: COLUMN action_param.rule_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_param.rule_text IS '接口 rule';


--
-- Name: action_param_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_param ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_param_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_run (
    action_type_code character varying(64) NOT NULL,
    action_name character varying(64) NOT NULL,
    type_version character varying(16),
    source_type character varying(16) NOT NULL,
    source_label character varying(16) NOT NULL,
    rule_code character varying(64),
    rule_version character varying(16),
    alert_id bigint,
    qa_session character varying(64),
    qa_session_title character varying(200),
    scenario_code character varying(64),
    scenario_name character varying(200),
    obj_kind character varying(32),
    obj_id character varying(64),
    obj_label character varying(200),
    params jsonb,
    params_text text,
    elapsed_ms integer,
    result_code character varying(200),
    retry_count integer,
    initiator character varying(64) NOT NULL,
    confirmer character varying(64),
    approver character varying(64),
    idem_key character varying(128) NOT NULL,
    biz_key character varying(128),
    status character varying(16) NOT NULL,
    sys_code character varying(64),
    ext_ref character varying(128),
    fail_reason text,
    unknown_reason text,
    reconcile_result character varying(16),
    reconcile_note text,
    reconcile_by character varying(64),
    withdraw_reason text,
    retry_of_id bigint,
    retried_as_id bigint,
    submitted_at timestamp with time zone NOT NULL,
    confirmed_at timestamp with time zone,
    approved_at timestamp with time zone,
    finished_at timestamp with time zone,
    in_center boolean DEFAULT true NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    review_snapshot jsonb,
    CONSTRAINT ck_action_run_actionstatus CHECK (((status)::text = ANY ((ARRAY['staged'::character varying, 'approving'::character varying, 'executing'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'unknown'::character varying, 'withdrawn'::character varying, 'reconciled'::character varying])::text[]))),
    CONSTRAINT ck_action_run_reconcileresult CHECK (((reconcile_result)::text = ANY ((ARRAY['succeeded'::character varying, 'not_executed'::character varying])::text[]))),
    CONSTRAINT ck_action_run_sourcetype CHECK (((source_type)::text = ANY ((ARRAY['rule'::character varying, 'alert'::character varying, 'alert_ops'::character varying, 'qa'::character varying, 'manual'::character varying, 'scenario'::character varying])::text[])))
);

ALTER TABLE ONLY public.action_run FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_run; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_run IS '动作执行记录（动作中心 + 动作类型执行记录）';


--
-- Name: COLUMN action_run.action_type_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.action_type_code IS '动作类型编号（软引用 action_type，FK to add later）';


--
-- Name: COLUMN action_run.action_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.action_name IS '动作名称（前端 action）';


--
-- Name: COLUMN action_run.source_label; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.source_label IS '来源原文';


--
-- Name: COLUMN action_run.rule_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.rule_code IS '软引用 rule，FK to add later';


--
-- Name: COLUMN action_run.qa_session; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.qa_session IS '问数会话（软引用，FK to add later）';


--
-- Name: COLUMN action_run.scenario_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.scenario_code IS '软引用 scenario，FK to add later';


--
-- Name: COLUMN action_run.params_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.params_text IS '参数摘要（ACTIONS.params / RUNS.summary）';


--
-- Name: COLUMN action_run.sys_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.sys_code IS '外部系统编号（软引用 ext_system，FK to add later）';


--
-- Name: COLUMN action_run.in_center; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.in_center IS '[推断] 是否出现在动作中心（ACTIONS 与新建为 true；RUNS 历史为 false）';


--
-- Name: COLUMN action_run.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.code IS '业务编号（前端 id）';


--
-- Name: COLUMN action_run.review_snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run.review_snapshot IS '暂存时的人工复核项与声明效果；旧记录为空';


--
-- Name: action_run_daily_counter; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_run_daily_counter (
    id bigint NOT NULL,
    day character varying(8) NOT NULL,
    last_value bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.action_run_daily_counter FORCE ROW LEVEL SECURITY;


--
-- Name: action_run_daily_counter_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_run_daily_counter ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_run_daily_counter_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_run_daily_stat; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_run_daily_stat (
    action_type_id bigint NOT NULL,
    stat_date date NOT NULL,
    ok integer NOT NULL,
    failed integer NOT NULL,
    unknown integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.action_run_daily_stat FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_run_daily_stat; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_run_daily_stat IS '动作类型每日执行量';


--
-- Name: action_run_daily_stat_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_run_daily_stat ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_run_daily_stat_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_run_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_run_log (
    action_run_id bigint NOT NULL,
    seq smallint NOT NULL,
    at timestamp with time zone NOT NULL,
    text text NOT NULL,
    actor text,
    level character varying(16),
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_action_run_log_timelinestatus CHECK (((level)::text = ANY ((ARRAY['done'::character varying, 'warn'::character varying, 'error'::character varying, 'unknown'::character varying])::text[])))
);

ALTER TABLE ONLY public.action_run_log FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_run_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_run_log IS '动作执行时间线（只追加）';


--
-- Name: COLUMN action_run_log.actor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run_log.actor IS '前端 ActionLog.desc 原文（说明 / 操作人）';


--
-- Name: COLUMN action_run_log.level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_run_log.level IS '前端 status';


--
-- Name: action_run_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_run_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_run_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_type (
    name character varying(64) NOT NULL,
    api character varying(64) NOT NULL,
    grp character varying(32) NOT NULL,
    status character varying(16) NOT NULL,
    version character varying(16) NOT NULL,
    target character varying(64) NOT NULL,
    target_api character varying(64) NOT NULL,
    target_kind character varying(64),
    owner character varying(64) NOT NULL,
    desc_tpl text NOT NULL,
    runners text NOT NULL,
    approval text NOT NULL,
    sys_id bigint,
    endpoint character varying(255) NOT NULL,
    biz_key_template character varying(255) NOT NULL,
    retired_release_id bigint,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    http_mapping jsonb,
    CONSTRAINT ck_action_type_typestatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'changing'::character varying, 'draft'::character varying])::text[])))
);

ALTER TABLE ONLY public.action_type FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_type IS '动作类型（当前版本）';


--
-- Name: COLUMN action_type.api; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.api IS 'API 名（唯一）';


--
-- Name: COLUMN action_type.grp; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.grp IS '分组（接口 group）';


--
-- Name: COLUMN action_type.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.version IS '当前版本 vN';


--
-- Name: COLUMN action_type.target_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.target_kind IS '对象类型前端 id（软引用，静态合并后改外键）';


--
-- Name: COLUMN action_type.desc_tpl; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.desc_tpl IS '说明模板（{sys} 占位）';


--
-- Name: COLUMN action_type.sys_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.sys_id IS '写回外部系统（ext_system.id），空 = 本体内部';


--
-- Name: COLUMN action_type.biz_key_template; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.biz_key_template IS '接口 idemKey';


--
-- Name: COLUMN action_type.retired_release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.retired_release_id IS '整版回滚下线于哪一版（ontology_release.id）';


--
-- Name: COLUMN action_type.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type.code IS '业务编号（前端 id）';


--
-- Name: action_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_type_rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_type_rule (
    action_type_id bigint NOT NULL,
    rule_code character varying(32) NOT NULL,
    seq integer NOT NULL,
    mode_text text,
    generates boolean NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.action_type_rule FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_type_rule; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_type_rule IS '动作类型 ↔ 规则';


--
-- Name: COLUMN action_type_rule.rule_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_rule.rule_code IS '规则编码 R-…（软引用）';


--
-- Name: COLUMN action_type_rule.mode_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_rule.mode_text IS '使用方式原文';


--
-- Name: COLUMN action_type_rule.generates; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_rule.generates IS '规则命中生成本动作（RULE_ACTION；动态合并后改读 rule）';


--
-- Name: action_type_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_type_rule ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_type_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: action_type_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_type_version (
    action_type_id bigint NOT NULL,
    ver character varying(16) NOT NULL,
    release_ver character varying(32) NOT NULL,
    change_note text NOT NULL,
    by_name character varying(64) NOT NULL,
    at timestamp with time zone NOT NULL,
    definition jsonb,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.action_type_version FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE action_type_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.action_type_version IS '动作类型版本（只追加）';


--
-- Name: COLUMN action_type_version.release_ver; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_version.release_ver IS '本体发布版本号（软引用）';


--
-- Name: COLUMN action_type_version.change_note; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_version.change_note IS '接口 change';


--
-- Name: COLUMN action_type_version.by_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_version.by_name IS '接口 by（姓名原文）';


--
-- Name: COLUMN action_type_version.definition; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.action_type_version.definition IS '该版定义（seed 没有，留空）';


--
-- Name: action_type_version_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.action_type_version ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.action_type_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: alert; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert (
    rule_code character varying(64) NOT NULL,
    rule_version character varying(16),
    hit_code character varying(64),
    dedupe_key character varying(200),
    level character varying(16) NOT NULL,
    obj_kind character varying(32) NOT NULL,
    obj_id character varying(64) NOT NULL,
    obj_label character varying(200) NOT NULL,
    value character varying(200) NOT NULL,
    threshold character varying(200) NOT NULL,
    triggered_at timestamp with time zone NOT NULL,
    data_cutoff timestamp with time zone NOT NULL,
    status character varying(16) NOT NULL,
    assignee character varying(64),
    close_reason text,
    action_run_id bigint,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    dims jsonb NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_alert_alertlevel CHECK (((level)::text = ANY ((ARRAY['high'::character varying, 'medium'::character varying, 'low'::character varying])::text[]))),
    CONSTRAINT ck_alert_alertstatus CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'processing'::character varying, 'resolved'::character varying, 'ignored'::character varying])::text[])))
);

ALTER TABLE ONLY public.alert FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE alert; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.alert IS '告警（规则命中生成，人工处理）';


--
-- Name: COLUMN alert.rule_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.rule_code IS '规则编号（软引用 rule.code，FK to add later）';


--
-- Name: COLUMN alert.rule_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.rule_version IS '命中时的规则版本（v3）';


--
-- Name: COLUMN alert.hit_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.hit_code IS '命中记录编号（软引用 rule_hit，FK to add later）';


--
-- Name: COLUMN alert.obj_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.obj_kind IS 'product/store/order（软引用对象类型）';


--
-- Name: COLUMN alert.value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.value IS '命中值（展示文本）';


--
-- Name: COLUMN alert.threshold; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.threshold IS '阈值（展示文本）';


--
-- Name: COLUMN alert.assignee; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.assignee IS '处理人姓名';


--
-- Name: COLUMN alert.action_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.action_run_id IS '关联动作（action_run.id；和 action_run.alert_id 互指，建表后再加约束）';


--
-- Name: COLUMN alert.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.code IS '业务编号（前端 id）';


--
-- Name: COLUMN alert.dims; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert.dims IS '通用维度值 [{objKind,id,label}]（来自规则去重键，如所属门店；列按维度生成）';


--
-- Name: alert_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.alert ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.alert_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: alert_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_log (
    alert_id bigint NOT NULL,
    seq smallint NOT NULL,
    at timestamp with time zone NOT NULL,
    actor character varying(64) NOT NULL,
    op character varying(16) NOT NULL,
    text text NOT NULL,
    from_status character varying(16),
    to_status character varying(16),
    abnormal boolean DEFAULT false NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_alert_log_alertop CHECK (((op)::text = ANY ((ARRAY['created'::character varying, 'claim'::character varying, 'transfer'::character varying, 'close'::character varying, 'start_action'::character varying, 'action_result'::character varying, 'note'::character varying])::text[])))
);

ALTER TABLE ONLY public.alert_log FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE alert_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.alert_log IS '告警处理日志（只追加）';


--
-- Name: COLUMN alert_log.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_log.seq IS '同一告警内的顺序（1 起）';


--
-- Name: COLUMN alert_log.actor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_log.actor IS '操作人姓名 / 系统';


--
-- Name: COLUMN alert_log.from_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_log.from_status IS 'AlertStatus code';


--
-- Name: COLUMN alert_log.to_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_log.to_status IS 'AlertStatus code';


--
-- Name: alert_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.alert_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.alert_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: api_key; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_key (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(40) NOT NULL,
    env character varying(8) NOT NULL,
    user_id bigint,
    token_hash character varying(64) NOT NULL,
    prefix character varying(16) NOT NULL,
    last4 character varying(4) NOT NULL,
    scopes jsonb NOT NULL,
    func_snapshot jsonb NOT NULL,
    ip_allow jsonb NOT NULL,
    note character varying(512) NOT NULL,
    creator character varying(128) NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    revoked_by character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_ids jsonb NOT NULL,
    principal_kind character varying(16) DEFAULT 'user'::character varying NOT NULL,
    service_account_id bigint,
    action_types jsonb,
    event_types jsonb,
    rate_per_minute integer,
    quota_per_day integer,
    function_types jsonb,
    CONSTRAINT ck_api_key_principal CHECK (((((principal_kind)::text = 'user'::text) AND (user_id IS NOT NULL) AND (service_account_id IS NULL)) OR (((principal_kind)::text = 'service'::text) AND (service_account_id IS NOT NULL) AND (user_id IS NULL))))
);


--
-- Name: api_key_call; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_key_call (
    id bigint NOT NULL,
    key_id bigint NOT NULL,
    api character varying(256) NOT NULL,
    ip character varying(64) NOT NULL,
    status_code integer NOT NULL,
    duration_ms integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL
);


--
-- Name: api_key_call_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.api_key_call ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.api_key_call_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: api_key_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.api_key ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.api_key_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: api_key_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_key_usage (
    id bigint NOT NULL,
    key_id bigint NOT NULL,
    period character varying(8) NOT NULL,
    bucket timestamp with time zone NOT NULL,
    calls integer NOT NULL
);


--
-- Name: COLUMN api_key_usage.period; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_key_usage.period IS 'minute / day';


--
-- Name: api_key_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.api_key_usage ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.api_key_usage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_export_job; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_export_job (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    user_id bigint NOT NULL,
    filters jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL
);


--
-- Name: COLUMN audit_export_job.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_export_job.code IS '业务编号（前端 id）';


--
-- Name: audit_export_job_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_export_job ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.audit_export_job_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_export_row; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_export_row (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    audit_id bigint NOT NULL
);


--
-- Name: audit_export_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_export_row ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.audit_export_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    "time" timestamp with time zone NOT NULL,
    actor character varying(256) NOT NULL,
    action character varying(64) NOT NULL,
    target character varying(512) NOT NULL,
    ip character varying(64) NOT NULL,
    result character varying(16) DEFAULT 'success'::character varying NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint,
    principal_kind character varying(16),
    service_account_code character varying(64),
    api_key_prefix character varying(16),
    CONSTRAINT ck_audit_log_auditresult CHECK (((result)::text = ANY ((ARRAY['success'::character varying, 'failure'::character varying, 'denied'::character varying])::text[])))
);


--
-- Name: TABLE audit_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.audit_log IS '审计日志（只追加）';


--
-- Name: COLUMN audit_log."time"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log."time" IS '发生时间';


--
-- Name: COLUMN audit_log.actor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.actor IS '操作人展示串「姓名（编号）」';


--
-- Name: COLUMN audit_log.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.action IS '动作（中文，如「导出」）';


--
-- Name: COLUMN audit_log.target; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.target IS '对象';


--
-- Name: COLUMN audit_log.ip; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.ip IS '来源 IP';


--
-- Name: COLUMN audit_log.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.code IS '业务编号（前端 id）';


--
-- Name: COLUMN audit_log.principal_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.principal_kind IS 'user / service（API 密钥调用）';


--
-- Name: COLUMN audit_log.service_account_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.service_account_code IS '服务账号编号（SA-0001）';


--
-- Name: COLUMN audit_log.api_key_prefix; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_log.api_key_prefix IS '调用密钥前缀（不含密钥）';


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: biz_order; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_order (
    code character varying(64) NOT NULL,
    store_id bigint NOT NULL,
    channel character varying(32) NOT NULL,
    status character varying(24) NOT NULL,
    created_at_biz timestamp with time zone NOT NULL,
    shipped_at timestamp with time zone,
    amount numeric(12,2) NOT NULL,
    discount numeric(12,2) DEFAULT '0'::numeric NOT NULL,
    warehouse character varying(64),
    waybill character varying(64),
    member_phone character varying(16),
    is_test boolean DEFAULT false NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_biz_order_orderstatus CHECK (((status)::text = ANY ((ARRAY['pending_payment'::character varying, 'picking'::character varying, 'pending_shipment'::character varying, 'shipped'::character varying, 'delivered'::character varying, 'cancelled'::character varying])::text[])))
);

ALTER TABLE ONLY public.biz_order FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE biz_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.biz_order IS '订单';


--
-- Name: COLUMN biz_order.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.biz_order.code IS '订单号（前端 id）';


--
-- Name: COLUMN biz_order.created_at_biz; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.biz_order.created_at_biz IS '下单时间（mock createdAt，带秒）';


--
-- Name: COLUMN biz_order.shipped_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.biz_order.shipped_at IS '出库时间（带秒）';


--
-- Name: COLUMN biz_order.amount; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.biz_order.amount IS '实付 = 行合计 − 优惠';


--
-- Name: COLUMN biz_order.member_phone; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.biz_order.member_phone IS '会员手机号（已脱敏 160****2039）';


--
-- Name: COLUMN biz_order.is_test; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.biz_order.is_test IS '是否测试单（本体属性 objectProps.ts:45 is_test；seed 全 false）';


--
-- Name: biz_order_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.biz_order ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.biz_order_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: change_request; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.change_request (
    title character varying(128) NOT NULL,
    reason text NOT NULL,
    owner character varying(64) NOT NULL,
    reviewer character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    level character varying(16) NOT NULL,
    changes integer NOT NULL,
    reject_reason character varying(128),
    release_id bigint,
    detail jsonb NOT NULL,
    status_at timestamp with time zone NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_change_request_crlevel CHECK (((level)::text = ANY ((ARRAY['major'::character varying, 'minor'::character varying, 'patch'::character varying])::text[]))),
    CONSTRAINT ck_change_request_crstatus CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'in_review'::character varying, 'approved'::character varying, 'rejected'::character varying, 'published'::character varying])::text[])))
);

ALTER TABLE ONLY public.change_request FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE change_request; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.change_request IS '变更单';


--
-- Name: COLUMN change_request.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.change_request.release_id IS '进入的版本（发布 / 回滚后写）';


--
-- Name: COLUMN change_request.detail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.change_request.detail IS '[推断] CRDetail 原样（checks/touches/synonyms…）';


--
-- Name: COLUMN change_request.status_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.change_request.status_at IS 'mock updatedAt';


--
-- Name: COLUMN change_request.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.change_request.code IS '业务编号（前端 id）';


--
-- Name: change_request_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.change_request ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.change_request_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cr_check; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cr_check (
    cr_id bigint NOT NULL,
    code character varying(8) NOT NULL,
    target_db character varying(8) NOT NULL,
    name character varying(64) NOT NULL,
    result character varying(8) NOT NULL,
    note text NOT NULL,
    checked_at timestamp with time zone NOT NULL,
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.cr_check FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE cr_check; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cr_check IS '变更单校验结果';


--
-- Name: COLUMN cr_check.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_check.code IS 'V1..V17';


--
-- Name: COLUMN cr_check.target_db; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_check.target_db IS ''''' | seed（种子基线）| dm8 | kes';


--
-- Name: COLUMN cr_check.result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_check.result IS 'block|warn|info|pass|na';


--
-- Name: cr_check_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cr_check ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cr_check_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cr_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cr_event (
    cr_id bigint NOT NULL,
    action character varying(16) NOT NULL,
    actor character varying(64) NOT NULL,
    actor_code character varying(32),
    text character varying(256) NOT NULL,
    "desc" text,
    level character varying(16),
    at timestamp with time zone NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.cr_event FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE cr_event; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cr_event IS '变更单评审记录（只追加）';


--
-- Name: COLUMN cr_event.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_event.action IS 'publish|rollback|…';


--
-- Name: COLUMN cr_event.actor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_event.actor IS '操作人姓名';


--
-- Name: COLUMN cr_event.actor_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_event.actor_code IS '操作人 sys_user.code';


--
-- Name: COLUMN cr_event.level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_event.level IS '前端 ReviewEvent.status：warn / error';


--
-- Name: cr_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cr_event ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cr_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cr_impact; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cr_impact (
    cr_id bigint NOT NULL,
    grp character varying(8) NOT NULL,
    kind character varying(32) NOT NULL,
    name character varying(128) NOT NULL,
    note text NOT NULL,
    cnt integer,
    ref_kind character varying(16),
    ref_id character varying(64),
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.cr_impact FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE cr_impact; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cr_impact IS '变更单影响面';


--
-- Name: COLUMN cr_impact.grp; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_impact.grp IS 'direct|defs|usage';


--
-- Name: cr_impact_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cr_impact ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cr_impact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cr_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cr_item (
    cr_id bigint NOT NULL,
    target_kind character varying(16) NOT NULL,
    target_id character varying(64) NOT NULL,
    section character varying(32) NOT NULL,
    item_key character varying(128) NOT NULL,
    op character varying(8) NOT NULL,
    before jsonb NOT NULL,
    after jsonb NOT NULL,
    type_label character varying(32),
    field_label character varying(128),
    before_text text,
    after_text text,
    updated_by character varying(64),
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.cr_item FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE cr_item; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cr_item IS '变更单改动行';


--
-- Name: COLUMN cr_item.section; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.section IS 'properties|links|params|conds…';


--
-- Name: COLUMN cr_item.item_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.item_key IS '行键（属性名、取值、条件号…）';


--
-- Name: COLUMN cr_item.op; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.op IS 'add|update|delete';


--
-- Name: COLUMN cr_item.type_label; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.type_label IS '改动列表「类型」列';


--
-- Name: COLUMN cr_item.field_label; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.field_label IS '改动列表「字段」列';


--
-- Name: COLUMN cr_item.before_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.before_text IS '改动列表显示文字（可空，空时由 before 生成）';


--
-- Name: COLUMN cr_item.updated_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_item.updated_by IS '保存人姓名';


--
-- Name: cr_item_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cr_item ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cr_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cr_target; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cr_target (
    cr_id bigint NOT NULL,
    target_kind character varying(16) NOT NULL,
    target_id character varying(64) NOT NULL,
    target_name character varying(128),
    is_new boolean NOT NULL,
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.cr_target FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE cr_target; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cr_target IS '变更单涉及的定义';


--
-- Name: COLUMN cr_target.target_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_target.target_kind IS 'object|link|metric|dimension|knowledge|action|event|rule';


--
-- Name: COLUMN cr_target.target_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_target.target_id IS '定义业务编码（前端 id）';


--
-- Name: COLUMN cr_target.target_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_target.target_name IS '显示名（改动列表「对象」列）';


--
-- Name: COLUMN cr_target.is_new; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cr_target.is_new IS '本单新建的定义';


--
-- Name: cr_target_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cr_target ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cr_target_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cr_trial; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cr_trial (
    cr_id bigint NOT NULL,
    period character varying(32) NOT NULL,
    before_val character varying(64),
    after_val character varying(64),
    delta character varying(32),
    scope character varying(128),
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.cr_trial FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE cr_trial; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cr_trial IS '变更单试算';


--
-- Name: cr_trial_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.cr_trial ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.cr_trial_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: datasource; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datasource (
    name character varying(128) NOT NULL,
    type character varying(16) NOT NULL,
    host character varying(255) NOT NULL,
    port integer NOT NULL,
    database character varying(128) NOT NULL,
    username character varying(128) NOT NULL,
    password_enc character varying(255),
    compat_mode character varying(32),
    database_mode character varying(16),
    erp_system character varying(64),
    app_id character varying(64),
    ssl boolean NOT NULL,
    status character varying(16) NOT NULL,
    last_test jsonb,
    owner character varying(64) NOT NULL,
    object_types character varying(64)[] NOT NULL,
    sync_mode character varying(16) NOT NULL,
    sync_cron character varying(64) NOT NULL,
    sync_cron_text character varying(64) NOT NULL,
    sync_window character varying(32) NOT NULL,
    sync_cursor_field character varying(64) NOT NULL,
    sync_next_run timestamp with time zone,
    table_count integer NOT NULL,
    created_on date NOT NULL,
    note text,
    ref_only jsonb,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_datasource_dsstatus CHECK (((status)::text = ANY ((ARRAY['ok'::character varying, 'error'::character varying, 'untested'::character varying])::text[]))),
    CONSTRAINT ck_datasource_dssyncmode CHECK (((sync_mode)::text = ANY ((ARRAY['incremental'::character varying, 'full'::character varying, 'cdc'::character varying, 'manual'::character varying])::text[]))),
    CONSTRAINT ck_datasource_dstype CHECK (((type)::text = ANY ((ARRAY['postgresql'::character varying, 'dm8'::character varying, 'kingbase'::character varying, 'mysql'::character varying, 'erp'::character varying])::text[])))
);

ALTER TABLE ONLY public.datasource FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE datasource; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.datasource IS '数据连接（data 模块）';


--
-- Name: COLUMN datasource.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.name IS '名称';


--
-- Name: COLUMN datasource.type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.type IS '类型';


--
-- Name: COLUMN datasource.host; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.host IS '主机';


--
-- Name: COLUMN datasource.port; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.port IS '端口';


--
-- Name: COLUMN datasource.database; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.database IS '库名（ERP：应用编码）';


--
-- Name: COLUMN datasource.username; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.username IS '用户名（接口 user）';


--
-- Name: COLUMN datasource.password_enc; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.password_enc IS '密码摘要（只写不读，接口不返回；seed 行为空）';


--
-- Name: COLUMN datasource.compat_mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.compat_mode IS '达梦兼容模式';


--
-- Name: COLUMN datasource.database_mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.database_mode IS '金仓 database_mode';


--
-- Name: COLUMN datasource.erp_system; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.erp_system IS 'ERP 系统';


--
-- Name: COLUMN datasource.app_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.app_id IS 'ERP appId';


--
-- Name: COLUMN datasource.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.status IS '连接状态';


--
-- Name: COLUMN datasource.last_test; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.last_test IS '最近一次测试 {time, ok, ms, msg}';


--
-- Name: COLUMN datasource.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.owner IS '负责人姓名（原文）';


--
-- Name: COLUMN datasource.object_types; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.object_types IS '映射的对象类型（中文名，mock 原样）';


--
-- Name: COLUMN datasource.sync_next_run; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.sync_next_run IS '下次运行';


--
-- Name: COLUMN datasource.table_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.table_count IS '库表数';


--
-- Name: COLUMN datasource.created_on; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.created_on IS '创建日期（接口 createdAt）';


--
-- Name: COLUMN datasource.note; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.note IS '详情顶部说明';


--
-- Name: COLUMN datasource.ref_only; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.ref_only IS '只作参考的对象 {objects, source}';


--
-- Name: COLUMN datasource.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource.code IS '业务编号（前端 id）';


--
-- Name: datasource_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.datasource ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.datasource_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: datasource_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datasource_run (
    datasource_id bigint NOT NULL,
    seq integer NOT NULL,
    run_at timestamp with time zone NOT NULL,
    result character varying(16) NOT NULL,
    rows integer NOT NULL,
    secs integer NOT NULL,
    msg text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_datasource_run_dsrunresult CHECK (((result)::text = ANY ((ARRAY['success'::character varying, 'delayed'::character varying, 'failed'::character varying])::text[])))
);

ALTER TABLE ONLY public.datasource_run FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE datasource_run; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.datasource_run IS '数据连接同步运行记录（data 模块）';


--
-- Name: COLUMN datasource_run.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_run.seq IS '展示顺序（0 = 最新）';


--
-- Name: COLUMN datasource_run.run_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_run.run_at IS '接口 time';


--
-- Name: datasource_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.datasource_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.datasource_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: datasource_table; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datasource_table (
    datasource_id bigint NOT NULL,
    seq integer NOT NULL,
    schema_name character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    comment character varying(255) NOT NULL,
    row_count bigint NOT NULL,
    col_count integer NOT NULL,
    object_type character varying(64),
    ref character varying(255),
    meta_updated_at timestamp with time zone NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.datasource_table FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE datasource_table; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.datasource_table IS '数据连接的库表清单（data 模块）';


--
-- Name: COLUMN datasource_table.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.seq IS '展示顺序';


--
-- Name: COLUMN datasource_table.schema_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.schema_name IS '接口 schema';


--
-- Name: COLUMN datasource_table.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.name IS '表名';


--
-- Name: COLUMN datasource_table.comment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.comment IS '说明';


--
-- Name: COLUMN datasource_table.row_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.row_count IS '接口 rows';


--
-- Name: COLUMN datasource_table.col_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.col_count IS '接口 cols';


--
-- Name: COLUMN datasource_table.object_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.object_type IS '映射的对象类型（中文名）';


--
-- Name: COLUMN datasource_table.ref; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.ref IS '参考表：以哪张表为准';


--
-- Name: COLUMN datasource_table.meta_updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.datasource_table.meta_updated_at IS '接口 updatedAt（源表最近更新）';


--
-- Name: datasource_table_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.datasource_table ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.datasource_table_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: decision_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decision_log (
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    code character varying(64) NOT NULL,
    caller_kind character varying(16) NOT NULL,
    caller_id character varying(64) NOT NULL,
    caller_name character varying(200),
    api_key_prefix character varying(32),
    ai_session_id character varying(64),
    ai_round_id character varying(64),
    obj_kind character varying(32) NOT NULL,
    obj_id character varying(64) NOT NULL,
    alert_code character varying(64),
    input jsonb NOT NULL,
    output jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL
);

ALTER TABLE ONLY public.decision_log FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE decision_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.decision_log IS '决策评估记录（只追加）';


--
-- Name: COLUMN decision_log.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.decision_log.code IS '业务编号（前端 id / decisionId）';


--
-- Name: COLUMN decision_log.caller_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.decision_log.caller_kind IS 'user / service_account / ai';


--
-- Name: COLUMN decision_log.input; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.decision_log.input IS '请求 + 评估时的告警 / 命中 / 生命周期上下文';


--
-- Name: COLUMN decision_log.output; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.decision_log.output IS '排序后的建议动作（含试运行与策略）';


--
-- Name: decision_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.decision_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.decision_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_level; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dim_level (
    dimension_id bigint NOT NULL,
    level_no integer NOT NULL,
    name character varying(64) NOT NULL,
    api_name character varying(64) NOT NULL,
    source character varying(256) NOT NULL,
    value_count integer NOT NULL,
    note text,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.dim_level FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE dim_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dim_level IS '维度层级';


--
-- Name: COLUMN dim_level.level_no; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_level.level_no IS '从 1 起';


--
-- Name: dim_level_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dim_level ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.dim_level_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_mapping; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dim_mapping (
    dimension_id bigint NOT NULL,
    map_key character varying(256) NOT NULL,
    phrase character varying(128) NOT NULL,
    targets jsonb NOT NULL,
    match character varying(16) NOT NULL,
    source character varying(24) NOT NULL,
    hits integer NOT NULL,
    conflict text,
    resolve text,
    draft_cr character varying(32),
    diff character varying(16),
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_dim_mapping_mappingsource CHECK (((source)::text = ANY ((ARRAY['value_synonym'::character varying, 'value_name'::character varying, 'knowledge'::character varying, 'qa_feedback'::character varying])::text[]))),
    CONSTRAINT ck_dim_mapping_matchmode CHECK (((match)::text = ANY ((ARRAY['exact'::character varying, 'contains'::character varying, 'multi'::character varying])::text[])))
);

ALTER TABLE ONLY public.dim_mapping FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE dim_mapping; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dim_mapping IS '维度说法映射';


--
-- Name: COLUMN dim_mapping.map_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_mapping.map_key IS 'mock key';


--
-- Name: COLUMN dim_mapping.hits; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_mapping.hits IS '命中次数（运行时）';


--
-- Name: dim_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dim_mapping ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.dim_mapping_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_value; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dim_value (
    dimension_id bigint NOT NULL,
    value_key character varying(128) NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    level_name character varying(64) NOT NULL,
    parent character varying(128),
    status character varying(16) NOT NULL,
    synonyms jsonb NOT NULL,
    record_count bigint,
    src character varying(256),
    note text,
    ref jsonb,
    since_release character varying(32),
    until_release character varying(32),
    draft_cr character varying(32),
    diff character varying(16),
    pub jsonb,
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_dim_value_dimvaluestatus CHECK (((status)::text = ANY ((ARRAY['enabled'::character varying, 'disabled'::character varying])::text[])))
);

ALTER TABLE ONLY public.dim_value FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE dim_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dim_value IS '维度取值';


--
-- Name: COLUMN dim_value.value_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_value.value_key IS 'mock key（层级:编码）';


--
-- Name: COLUMN dim_value.parent; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_value.parent IS '上级取值名称（mock parent 原文）';


--
-- Name: COLUMN dim_value.record_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_value.record_count IS 'mock count（运行时）';


--
-- Name: COLUMN dim_value.ref; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_value.ref IS '跳转引用（mock ref 原样）';


--
-- Name: COLUMN dim_value.draft_cr; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_value.draft_cr IS '[推断] 草稿所在变更单（mock draft）';


--
-- Name: COLUMN dim_value.pub; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dim_value.pub IS '草稿里改过的值的已发布原值（mock pub；当前种子没有）';


--
-- Name: dim_value_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dim_value ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.dim_value_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dimension; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dimension (
    api_name character varying(64) NOT NULL,
    display_name character varying(64) NOT NULL,
    kind character varying(16) NOT NULL,
    dim_group character varying(32) NOT NULL,
    status character varying(16) NOT NULL,
    owner character varying(64) NOT NULL,
    object_id bigint,
    object_name character varying(64) NOT NULL,
    attr character varying(128) NOT NULL,
    source_column character varying(256) NOT NULL,
    value_src character varying(256) NOT NULL,
    sync character varying(128) NOT NULL,
    description text NOT NULL,
    qa jsonb NOT NULL,
    levels_before jsonb,
    metric_dims jsonb NOT NULL,
    metric_note jsonb,
    usage jsonb NOT NULL,
    history jsonb NOT NULL,
    since_release character varying(32) NOT NULL,
    count_label character varying(64),
    values_note text,
    draft_note text,
    seq integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_dimension_defstatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'draft'::character varying, 'rejected'::character varying])::text[]))),
    CONSTRAINT ck_dimension_dimkind CHECK (((kind)::text = ANY ((ARRAY['dimension'::character varying, 'codelist'::character varying])::text[])))
);

ALTER TABLE ONLY public.dimension FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE dimension; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dimension IS '维度 / 码表';


--
-- Name: COLUMN dimension.object_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dimension.object_name IS 'mock objectName（原文）';


--
-- Name: COLUMN dimension.source_column; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dimension.source_column IS 'mock column';


--
-- Name: COLUMN dimension.qa; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dimension.qa IS '问数默认口径（原样）';


--
-- Name: COLUMN dimension.levels_before; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dimension.levels_before IS '[推断] 某版本之前的层级/口径（mock levelsBefore 原样）';


--
-- Name: COLUMN dimension.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dimension.code IS '业务编号（前端 id）';


--
-- Name: dimension_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dimension ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.dimension_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event (
    event_type_id bigint NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    obj_kind character varying(64),
    object_id character varying(64) NOT NULL,
    obj_label character varying(128) NOT NULL,
    summary text NOT NULL,
    outcome character varying(16) NOT NULL,
    subscriber_count integer NOT NULL,
    dedup_key character varying(200) NOT NULL,
    latency_ms integer,
    payload jsonb,
    is_test boolean DEFAULT false NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    source character varying(128),
    producer_type character varying(64),
    producer_object_type character varying(64),
    trace_id character varying(128),
    envelope_hash character varying(64),
    ingested_at timestamp with time zone,
    release_id bigint,
    definition_snapshot jsonb,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_event_eventoutcome CHECK (((outcome)::text = ANY ((ARRAY['delivered'::character varying, 'alert_created'::character varying, 'deduped'::character varying, 'partial'::character varying, 'failed'::character varying, 'pending'::character varying])::text[])))
);

ALTER TABLE ONLY public.event FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event IS '事件（近期事件流）';


--
-- Name: COLUMN event.occurred_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.occurred_at IS '接口 at';


--
-- Name: COLUMN event.obj_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.obj_kind IS 'store/product/order/action，可空';


--
-- Name: COLUMN event.object_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.object_id IS '接口 objId';


--
-- Name: COLUMN event.summary; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.summary IS '载荷摘要';


--
-- Name: COLUMN event.subscriber_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.subscriber_count IS '接口 subscribers';


--
-- Name: COLUMN event.payload; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.payload IS '原始载荷（seed 无）';


--
-- Name: COLUMN event.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.code IS '业务编号（前端 id）';


--
-- Name: COLUMN event.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.release_id IS '本体版本（ontology_release.id）';


--
-- Name: event_daily_stat; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_daily_stat (
    event_type_id bigint NOT NULL,
    stat_date date NOT NULL,
    value integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.event_daily_stat FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event_daily_stat; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_daily_stat IS '事件类型按天统计';


--
-- Name: event_daily_stat_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_daily_stat ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_daily_stat_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_dedup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_dedup (
    id bigint NOT NULL,
    event_type_id bigint NOT NULL,
    dedup_key character varying(200) NOT NULL,
    last_accepted_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.event_dedup FORCE ROW LEVEL SECURITY;


--
-- Name: event_dedup_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_dedup ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_dedup_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_delivery; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_delivery (
    id bigint NOT NULL,
    event_id bigint NOT NULL,
    subscriber_name character varying(128) NOT NULL,
    subscription jsonb NOT NULL,
    state character varying(16) NOT NULL,
    attempts integer NOT NULL,
    next_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    error character varying(128),
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.event_delivery FORCE ROW LEVEL SECURITY;


--
-- Name: event_delivery_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_delivery ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_delivery_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_delivery_record; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_delivery_record (
    id bigint NOT NULL,
    delivery_id bigint NOT NULL,
    kind character varying(32) NOT NULL,
    attempt integer NOT NULL,
    state character varying(16) NOT NULL,
    error character varying(128),
    result jsonb,
    detail jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.event_delivery_record FORCE ROW LEVEL SECURITY;


--
-- Name: event_delivery_record_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_delivery_record ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_delivery_record_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_envelope_field; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_envelope_field (
    seq integer NOT NULL,
    name character varying(64) NOT NULL,
    api character varying(64) NOT NULL,
    type character varying(64) NOT NULL,
    required boolean NOT NULL,
    description text NOT NULL,
    sample text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.event_envelope_field FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event_envelope_field; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_envelope_field IS '事件信封字段（全局）';


--
-- Name: event_envelope_field_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_envelope_field ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_envelope_field_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_payload_field; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_payload_field (
    event_type_id bigint NOT NULL,
    seq integer NOT NULL,
    name character varying(64) NOT NULL,
    api character varying(64) NOT NULL,
    type character varying(64) NOT NULL,
    required boolean NOT NULL,
    description text NOT NULL,
    sample text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    schema jsonb
);

ALTER TABLE ONLY public.event_payload_field FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event_payload_field; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_payload_field IS '事件载荷字段';


--
-- Name: COLUMN event_payload_field.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_payload_field.description IS '接口 desc';


--
-- Name: event_payload_field_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_payload_field ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_payload_field_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_subscription; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_subscription (
    event_type_id bigint NOT NULL,
    seq integer NOT NULL,
    kind character varying(16) NOT NULL,
    name character varying(128) NOT NULL,
    rule_code character varying(32),
    filter_text text NOT NULL,
    filter_expr jsonb,
    then_text text NOT NULL,
    status character varying(16) NOT NULL,
    weekly integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_event_subscription_subscriberkind CHECK (((kind)::text = ANY ((ARRAY['rule'::character varying, 'automation'::character varying, 'notify'::character varying, 'external'::character varying])::text[]))),
    CONSTRAINT ck_event_subscription_subscriberstatus CHECK (((status)::text = ANY ((ARRAY['enabled'::character varying, 'disabled'::character varying, 'error'::character varying])::text[])))
);

ALTER TABLE ONLY public.event_subscription FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event_subscription; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_subscription IS '事件订阅方';


--
-- Name: COLUMN event_subscription.rule_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_subscription.rule_code IS '规则 R-…（软引用，接口 ruleId）';


--
-- Name: COLUMN event_subscription.filter_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_subscription.filter_text IS '接口 filter';


--
-- Name: COLUMN event_subscription.then_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_subscription.then_text IS '接口 then';


--
-- Name: COLUMN event_subscription.weekly; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_subscription.weekly IS '近 7 天投递数 [推断]';


--
-- Name: event_subscription_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_subscription ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_subscription_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_type (
    name character varying(64) NOT NULL,
    api character varying(64) NOT NULL,
    source character varying(16) NOT NULL,
    source_ref text NOT NULL,
    rule_code character varying(32),
    object character varying(64) NOT NULL,
    object_api character varying(64) NOT NULL,
    obj_kind character varying(64),
    status character varying(16) NOT NULL,
    version character varying(16) NOT NULL,
    owner character varying(64) NOT NULL,
    description text NOT NULL,
    dedup_key_rule text NOT NULL,
    dedup_window character varying(64) NOT NULL,
    delivery character varying(32) NOT NULL,
    ordering character varying(64) NOT NULL,
    retention_days integer NOT NULL,
    weekly integer NOT NULL,
    retired_release_id bigint,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_event_type_eventsource CHECK (((source)::text = ANY ((ARRAY['sync'::character varying, 'rule'::character varying, 'action'::character varying, 'external'::character varying])::text[]))),
    CONSTRAINT ck_event_type_typestatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'changing'::character varying, 'draft'::character varying])::text[])))
);

ALTER TABLE ONLY public.event_type FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_type IS '事件类型（当前版本）';


--
-- Name: COLUMN event_type.rule_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.rule_code IS '来源规则 R-…（软引用，接口 ruleId）';


--
-- Name: COLUMN event_type.obj_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.obj_kind IS '对象类型前端 id（软引用）';


--
-- Name: COLUMN event_type.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.description IS '接口 desc';


--
-- Name: COLUMN event_type.dedup_key_rule; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.dedup_key_rule IS '接口 dedupKey';


--
-- Name: COLUMN event_type.dedup_window; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.dedup_window IS '原文';


--
-- Name: COLUMN event_type.retention_days; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.retention_days IS '接口 retention = ''N 天''';


--
-- Name: COLUMN event_type.weekly; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.weekly IS '近 7 天事件数 [推断]';


--
-- Name: COLUMN event_type.retired_release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.retired_release_id IS '整版回滚下线于哪一版（ontology_release.id）';


--
-- Name: COLUMN event_type.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type.code IS '业务编号（前端 id）';


--
-- Name: event_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: event_type_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_type_version (
    event_type_id bigint NOT NULL,
    ver character varying(16) NOT NULL,
    release_ver character varying(32) NOT NULL,
    change_note text NOT NULL,
    by_name character varying(64) NOT NULL,
    at timestamp with time zone NOT NULL,
    definition jsonb,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.event_type_version FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE event_type_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_type_version IS '事件类型版本（只追加）';


--
-- Name: COLUMN event_type_version.release_ver; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event_type_version.release_ver IS '本体发布版本号（软引用）';


--
-- Name: event_type_version_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.event_type_version ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.event_type_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ext_system; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_system (
    name character varying(64) NOT NULL,
    vendor character varying(64) NOT NULL,
    category character varying(32) NOT NULL,
    endpoint character varying(255) NOT NULL,
    auth_type character varying(64) NOT NULL,
    auth_detail text NOT NULL,
    idempotent boolean NOT NULL,
    idem_header character varying(64) NOT NULL,
    timeout_ms integer NOT NULL,
    max_retry integer NOT NULL,
    retry_text text NOT NULL,
    rate_limit character varying(64) NOT NULL,
    owner character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    env character varying(16) NOT NULL,
    usage jsonb,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    local_only boolean DEFAULT false NOT NULL,
    action_authorizations jsonb,
    probe_path character varying(255),
    CONSTRAINT ck_ext_system_extsystemstatus CHECK (((status)::text = ANY ((ARRAY['ok'::character varying, 'degraded'::character varying, 'disabled'::character varying])::text[])))
);

ALTER TABLE ONLY public.ext_system FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE ext_system; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ext_system IS '外部系统（integration 模块）';


--
-- Name: COLUMN ext_system.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.name IS '名称（唯一）';


--
-- Name: COLUMN ext_system.endpoint; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.endpoint IS '基础地址';


--
-- Name: COLUMN ext_system.auth_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.auth_type IS '接口 auth';


--
-- Name: COLUMN ext_system.auth_detail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.auth_detail IS '脱敏说明（接口 authDetail）';


--
-- Name: COLUMN ext_system.idem_header; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.idem_header IS '幂等请求头（接口 idemKey）';


--
-- Name: COLUMN ext_system.retry_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.retry_text IS '重试说明原文（接口 retry）';


--
-- Name: COLUMN ext_system.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.owner IS '负责人姓名（原文）';


--
-- Name: COLUMN ext_system.env; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.env IS '环境原文（生产/测试）';


--
-- Name: COLUMN ext_system.usage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.usage IS '用途说明 {text, ops, actor?}（mock 原样）';


--
-- Name: COLUMN ext_system.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ext_system.code IS '业务编号（前端 id）';


--
-- Name: ext_system_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ext_system ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ext_system_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fn_param; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fn_param (
    id bigint NOT NULL,
    function_id bigint NOT NULL,
    direction character varying(3) NOT NULL,
    seq integer NOT NULL,
    name character varying(64) NOT NULL,
    type character varying(128) NOT NULL,
    obj_type character varying(128),
    "desc" text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.fn_param FORCE ROW LEVEL SECURITY;


--
-- Name: fn_param_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.fn_param ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.fn_param_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fn_use; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fn_use (
    id bigint NOT NULL,
    function_id bigint NOT NULL,
    kind character varying(16) NOT NULL,
    name character varying(128) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.fn_use FORCE ROW LEVEL SECURITY;


--
-- Name: fn_use_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.fn_use ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.fn_use_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fn_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fn_version (
    id bigint NOT NULL,
    function_id bigint NOT NULL,
    ver character varying(32) NOT NULL,
    at timestamp with time zone NOT NULL,
    author_id bigint NOT NULL,
    note text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.fn_version FORCE ROW LEVEL SECURITY;


--
-- Name: fn_version_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.fn_version ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.fn_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: function; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.function (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(64) NOT NULL,
    title character varying(128) NOT NULL,
    runtime character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    owner_id bigint NOT NULL,
    timeout_s integer NOT NULL,
    description text NOT NULL,
    code_text text NOT NULL,
    sample_in jsonb NOT NULL,
    sample_out jsonb,
    avg_ms integer DEFAULT 0 NOT NULL,
    calls_7d integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.function FORCE ROW LEVEL SECURITY;


--
-- Name: COLUMN function.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.function.code IS '业务编号（前端 id）';


--
-- Name: function_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.function ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.function_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: governed_binding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.governed_binding (
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    object_code character varying(64) NOT NULL,
    table_name character varying(63) NOT NULL,
    read_columns jsonb NOT NULL,
    write_columns jsonb NOT NULL,
    supplementary jsonb
);

ALTER TABLE ONLY public.governed_binding FORCE ROW LEVEL SECURITY;


--
-- Name: governed_binding_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.governed_binding ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.governed_binding_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: knowledge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge (
    name character varying(128) NOT NULL,
    type character varying(24) NOT NULL,
    status character varying(16) NOT NULL,
    definition text NOT NULL,
    calc text NOT NULL,
    synonyms jsonb NOT NULL,
    period character varying(128) NOT NULL,
    owner character varying(64) NOT NULL,
    maps text NOT NULL,
    filters jsonb NOT NULL,
    tries jsonb NOT NULL,
    periods jsonb,
    refs jsonb NOT NULL,
    history jsonb NOT NULL,
    draft jsonb,
    since_release character varying(32),
    stopped_release character varying(32),
    cr_code character varying(32),
    content_updated_at timestamp with time zone NOT NULL,
    seq integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_knowledge_knowledgestatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'draft'::character varying, 'disabled'::character varying])::text[]))),
    CONSTRAINT ck_knowledge_knowledgetype CHECK (((type)::text = ANY ((ARRAY['term'::character varying, 'rule'::character varying, 'fiscal_calendar'::character varying, 'metric_note'::character varying])::text[])))
);

ALTER TABLE ONLY public.knowledge FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE knowledge; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.knowledge IS '业务知识 / 术语';


--
-- Name: COLUMN knowledge.draft; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.knowledge.draft IS '[推断] 草稿内容（应进 cr_item）';


--
-- Name: COLUMN knowledge.stopped_release; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.knowledge.stopped_release IS 'mock stoppedIn';


--
-- Name: COLUMN knowledge.content_updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.knowledge.content_updated_at IS 'mock updatedAt';


--
-- Name: COLUMN knowledge.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.knowledge.code IS '业务编号（前端 id）';


--
-- Name: knowledge_apply; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_apply (
    knowledge_id bigint NOT NULL,
    target_kind character varying(16) NOT NULL,
    target_code character varying(64) NOT NULL,
    target_name character varying(128) NOT NULL,
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_knowledge_apply_applykind CHECK (((target_kind)::text = ANY ((ARRAY['object_type'::character varying, 'metric'::character varying])::text[])))
);

ALTER TABLE ONLY public.knowledge_apply FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE knowledge_apply; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.knowledge_apply IS '知识适用范围';


--
-- Name: knowledge_apply_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.knowledge_apply ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.knowledge_apply_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: knowledge_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.knowledge ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.knowledge_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lifecycle; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lifecycle (
    object_type_id bigint NOT NULL,
    object_name character varying(64) NOT NULL,
    version integer,
    field character varying(128) NOT NULL,
    owner character varying(64) NOT NULL,
    updated_on timestamp with time zone NOT NULL,
    rule_code character varying(32),
    "desc" text NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.lifecycle FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE lifecycle; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lifecycle IS '对象类型生命周期（当前版本指针）';


--
-- Name: COLUMN lifecycle.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle.version IS '当前生效版本；null = 回滚后不在快照里';


--
-- Name: COLUMN lifecycle.updated_on; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle.updated_on IS 'mock updatedAt';


--
-- Name: COLUMN lifecycle.rule_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle.rule_code IS '状态机校验规则编号（SM-ORD，非 rule 表）';


--
-- Name: COLUMN lifecycle.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle.code IS '业务编号（前端 id）';


--
-- Name: lifecycle_change; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lifecycle_change (
    lifecycle_id bigint NOT NULL,
    at timestamp with time zone NOT NULL,
    obj_id character varying(64) NOT NULL,
    obj_label character varying(200) NOT NULL,
    from_state character varying(64) NOT NULL,
    to_state character varying(64) NOT NULL,
    transition_id bigint,
    by character varying(200) NOT NULL,
    alert_id bigint,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.lifecycle_change FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE lifecycle_change; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lifecycle_change IS '对象状态变化记录（数据同步逐条；只追加）';


--
-- Name: COLUMN lifecycle_change.transition_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_change.transition_id IS '命中的迁移；null = 非法迁移';


--
-- Name: COLUMN lifecycle_change.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_change.code IS '业务编号（前端 id）';


--
-- Name: lifecycle_change_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_change ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.lifecycle_change_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lifecycle_draft; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lifecycle_draft (
    lifecycle_id bigint NOT NULL,
    cr_code character varying(32) NOT NULL,
    edits jsonb NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.lifecycle_draft FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE lifecycle_draft; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lifecycle_draft IS '生命周期草稿改动（LcDraft，暂代 cr_item）';


--
-- Name: COLUMN lifecycle_draft.cr_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_draft.cr_code IS '变更单号（软引用，FK to add later）';


--
-- Name: COLUMN lifecycle_draft.edits; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_draft.edits IS '{addStates,addTransitions,editTransitions,removeTransitions}';


--
-- Name: lifecycle_draft_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_draft ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.lifecycle_draft_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lifecycle_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.lifecycle_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lifecycle_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lifecycle_state (
    version_id bigint NOT NULL,
    seq integer NOT NULL,
    key character varying(64) NOT NULL,
    name character varying(64) NOT NULL,
    kind character varying(16) NOT NULL,
    "desc" text NOT NULL,
    sla character varying(200),
    count bigint NOT NULL,
    x integer NOT NULL,
    y integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_lifecycle_state_lcstatekind CHECK (((kind)::text = ANY ((ARRAY['initial'::character varying, 'middle'::character varying, 'final'::character varying])::text[])))
);

ALTER TABLE ONLY public.lifecycle_state FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE lifecycle_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lifecycle_state IS '生命周期状态';


--
-- Name: COLUMN lifecycle_state.count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_state.count IS '当前处于该状态的对象数';


--
-- Name: COLUMN lifecycle_state.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_state.code IS '业务编号（前端 id）';


--
-- Name: lifecycle_state_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_state ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.lifecycle_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lifecycle_transition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lifecycle_transition (
    version_id bigint NOT NULL,
    seq integer NOT NULL,
    tr_code character varying(16) NOT NULL,
    from_state_id bigint NOT NULL,
    to_state_id bigint NOT NULL,
    trigger character varying(16) NOT NULL,
    by character varying(200) NOT NULL,
    event_type_id bigint,
    action_type_id bigint,
    guard text NOT NULL,
    effect text NOT NULL,
    weekly integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_lifecycle_transition_lctrigger CHECK (((trigger)::text = ANY ((ARRAY['event'::character varying, 'action'::character varying, 'system'::character varying, 'manual'::character varying])::text[])))
);

ALTER TABLE ONLY public.lifecycle_transition FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE lifecycle_transition; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lifecycle_transition IS '生命周期迁移';


--
-- Name: COLUMN lifecycle_transition.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_transition.code IS '业务编号（前端 id）';


--
-- Name: lifecycle_transition_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_transition ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.lifecycle_transition_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lifecycle_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lifecycle_version (
    lifecycle_id bigint NOT NULL,
    version integer NOT NULL,
    at timestamp with time zone NOT NULL,
    by character varying(64) NOT NULL,
    change text NOT NULL,
    undo jsonb,
    snapshot jsonb,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    release_id bigint,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.lifecycle_version FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE lifecycle_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lifecycle_version IS '生命周期版本（只追加）';


--
-- Name: COLUMN lifecycle_version.snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_version.snapshot IS '{states, transitions}';


--
-- Name: COLUMN lifecycle_version.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_version.code IS '业务编号（前端 id）';


--
-- Name: COLUMN lifecycle_version.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lifecycle_version.release_id IS '本体版本（ontology_release.id）';


--
-- Name: lifecycle_version_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_version ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.lifecycle_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: link_quality; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.link_quality (
    link_type_id bigint NOT NULL,
    hit numeric(7,3) NOT NULL,
    orphans bigint NOT NULL,
    nulls bigint NOT NULL,
    dup bigint NOT NULL,
    checked_at timestamp with time zone NOT NULL,
    note text,
    orphan_samples jsonb,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.link_quality FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE link_quality; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.link_quality IS '关系数据质量';


--
-- Name: COLUMN link_quality.hit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_quality.hit IS '命中率 %';


--
-- Name: link_quality_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.link_quality ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.link_quality_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: link_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.link_type (
    api_name character varying(64),
    display_name character varying(64) NOT NULL,
    from_code character varying(64) NOT NULL,
    to_code character varying(64) NOT NULL,
    from_object_id bigint,
    to_object_id bigint,
    cardinality character varying(16) NOT NULL,
    key_expr character varying(256) NOT NULL,
    from_prop character varying(64) NOT NULL,
    to_prop character varying(64) NOT NULL,
    via character varying(64),
    join_sql text NOT NULL,
    reverse_name character varying(64) NOT NULL,
    nullable boolean NOT NULL,
    description text NOT NULL,
    status character varying(16) NOT NULL,
    since_release character varying(32),
    cr_code character varying(32),
    owner character varying(64) NOT NULL,
    usage jsonb NOT NULL,
    history jsonb NOT NULL,
    seq integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_link_type_cardinality CHECK (((cardinality)::text = ANY ((ARRAY['one_to_one'::character varying, 'one_to_many'::character varying, 'many_to_one'::character varying, 'many_to_many'::character varying])::text[]))),
    CONSTRAINT ck_link_type_defstatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'draft'::character varying, 'rejected'::character varying])::text[])))
);

ALTER TABLE ONLY public.link_type FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE link_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.link_type IS '关系类型';


--
-- Name: COLUMN link_type.api_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.api_name IS 'API 名（mock api，缺省 = id）';


--
-- Name: COLUMN link_type.from_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.from_code IS '源对象前端 id';


--
-- Name: COLUMN link_type.to_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.to_code IS '目标对象前端 id';


--
-- Name: COLUMN link_type.to_object_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.to_object_id IS '[推断] 目标还不是对象类型时为空';


--
-- Name: COLUMN link_type.key_expr; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.key_expr IS '关联键（mock key）';


--
-- Name: COLUMN link_type.via; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.via IS '中间表';


--
-- Name: COLUMN link_type.since_release; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.since_release IS '首次发布版本号 [推断] 字符串';


--
-- Name: COLUMN link_type.cr_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.cr_code IS '变更单号（软引用）';


--
-- Name: COLUMN link_type.usage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.usage IS '引用（mock 原样）';


--
-- Name: COLUMN link_type.history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.history IS '变更历史';


--
-- Name: COLUMN link_type.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.link_type.code IS '业务编号（前端 id）';


--
-- Name: link_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.link_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.link_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: maintenance_asset_demo_20261028; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_asset_demo_20261028 (
    id bigint NOT NULL,
    ontology_id bigint NOT NULL,
    code character varying(64) NOT NULL,
    status character varying(32),
    maintenance_at timestamp with time zone,
    lock_version integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.maintenance_asset_demo_20261028 FORCE ROW LEVEL SECURITY;


--
-- Name: maintenance_asset_demo_20261028_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.maintenance_asset_demo_20261028 ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.maintenance_asset_demo_20261028_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: maintenance_asset_extra_demo_20261028; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_asset_extra_demo_20261028 (
    id bigint NOT NULL,
    ontology_id bigint NOT NULL,
    asset_id bigint NOT NULL,
    calibration_note text
);

ALTER TABLE ONLY public.maintenance_asset_extra_demo_20261028 FORCE ROW LEVEL SECURITY;


--
-- Name: maintenance_quality_demo_20261028; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_quality_demo_20261028 (
    id bigint NOT NULL,
    ontology_id bigint NOT NULL,
    code text,
    contact text,
    inspected_at timestamp with time zone
);

ALTER TABLE ONLY public.maintenance_quality_demo_20261028 FORCE ROW LEVEL SECURITY;


--
-- Name: maintenance_task_demo_20261028; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_task_demo_20261028 (
    id bigint NOT NULL,
    ontology_id bigint NOT NULL,
    asset_id bigint NOT NULL,
    code character varying(64) NOT NULL,
    status character varying(32),
    lock_version integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.maintenance_task_demo_20261028 FORCE ROW LEVEL SECURITY;


--
-- Name: maintenance_task_demo_20261028_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.maintenance_task_demo_20261028 ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.maintenance_task_demo_20261028_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: metric; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metric (
    api_name character varying(64) NOT NULL,
    display_name character varying(64) NOT NULL,
    metric_group character varying(32) NOT NULL,
    unit character varying(16) NOT NULL,
    version character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    owner character varying(64) NOT NULL,
    review_cr character varying(32),
    review_note character varying(256),
    formula text,
    dims jsonb,
    value numeric(20,4),
    prev numeric(20,4),
    stat_updated_at timestamp with time zone,
    def_rows jsonb NOT NULL,
    dim_rows jsonb NOT NULL,
    version_rows jsonb NOT NULL,
    ref_rows jsonb NOT NULL,
    disallowed_rows jsonb NOT NULL,
    seq integer NOT NULL,
    stat_seq integer,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    variants jsonb DEFAULT '[]'::jsonb NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_metric_metricstatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'draft'::character varying, 'offline'::character varying])::text[])))
);

ALTER TABLE ONLY public.metric FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE metric; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.metric IS '指标（当前版本）';


--
-- Name: COLUMN metric.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.version IS '口径版本 v3';


--
-- Name: COLUMN metric.review_cr; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.review_cr IS '审核中的变更单号（review.cr）';


--
-- Name: COLUMN metric.formula; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.formula IS 'METRICS.formula（7 个指标有）';


--
-- Name: COLUMN metric.dims; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.dims IS '允许拆分的维度名（METRICS.dims）';


--
-- Name: COLUMN metric.value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.value IS '当前值（运行时，不进快照）';


--
-- Name: COLUMN metric.stat_updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.stat_updated_at IS 'METRICS.updatedAt（带秒）';


--
-- Name: COLUMN metric.disallowed_rows; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.disallowed_rows IS '不允许的维度 {dim,reason}（Metrics.vue DENY）';


--
-- Name: COLUMN metric.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.seq IS 'METRIC_LIST 顺序';


--
-- Name: COLUMN metric.stat_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.stat_seq IS 'METRICS 顺序（有统计值的 7 个）';


--
-- Name: COLUMN metric.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.code IS '业务编号（前端 id）';


--
-- Name: COLUMN metric.variants; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.metric.variants IS '口径变体 [{code,label,api,version,default}]';


--
-- Name: metric_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.metric ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.metric_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notification; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification (
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    recipient_id bigint NOT NULL,
    source_event_id bigint,
    cr_id bigint,
    title character varying(200) NOT NULL,
    description text NOT NULL,
    read_at timestamp with time zone,
    source_kind character varying(16) DEFAULT 'cr'::character varying NOT NULL,
    source_outbox_id bigint,
    target_code character varying(64),
    CONSTRAINT ck_notification_notification_source CHECK (((((source_kind)::text = 'cr'::text) AND (source_event_id IS NOT NULL) AND (cr_id IS NOT NULL) AND (source_outbox_id IS NULL)) OR (((source_kind)::text = 'page'::text) AND (source_event_id IS NULL) AND (cr_id IS NULL) AND (source_outbox_id IS NOT NULL) AND (target_code IS NOT NULL))))
);

ALTER TABLE ONLY public.notification FORCE ROW LEVEL SECURITY;


--
-- Name: notification_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.notification ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.notification_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: object_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.object_type (
    api_name character varying(64) NOT NULL,
    display_name character varying(64) NOT NULL,
    obj_group character varying(32) NOT NULL,
    domain character varying(64),
    description text NOT NULL,
    conflict_strategy character varying(24) NOT NULL,
    conflict_note character varying(128),
    lifecycle character varying(16) NOT NULL,
    sync character varying(128),
    sample_questions jsonb NOT NULL,
    qa_cards integer NOT NULL,
    qa30 integer NOT NULL,
    unused_props integer NOT NULL,
    source_table character varying(128),
    datasource_name character varying(64),
    owner character varying(64),
    pk_property character varying(64),
    record_count bigint,
    data_cutoff timestamp with time zone,
    status character varying(16) DEFAULT 'published'::character varying NOT NULL,
    seq integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_object_type_conflictstrategy CHECK (((conflict_strategy)::text = ANY ((ARRAY['user_edit_wins'::character varying, 'latest_source_ts'::character varying])::text[]))),
    CONSTRAINT ck_object_type_lifecycle CHECK (((lifecycle)::text = 'active'::text)),
    CONSTRAINT ck_object_type_objectstatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'draft'::character varying])::text[])))
);

ALTER TABLE ONLY public.object_type FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE object_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.object_type IS '对象类型';


--
-- Name: COLUMN object_type.api_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.api_name IS 'API 名（SalesOrder）';


--
-- Name: COLUMN object_type.obj_group; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.obj_group IS '分组（交易/客户/…）';


--
-- Name: COLUMN object_type.domain; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.domain IS '业务域（零售交易）';


--
-- Name: COLUMN object_type.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.description IS '说明（自然语言）';


--
-- Name: COLUMN object_type.conflict_note; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.conflict_note IS '冲突策略括号里的补充说明（mock 原文去掉主值后的部分）';


--
-- Name: COLUMN object_type.sync; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.sync IS '同步方式（文案）';


--
-- Name: COLUMN object_type.sample_questions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.sample_questions IS '样例问题 qs[{q,cert,query?}]';


--
-- Name: COLUMN object_type.qa_cards; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.qa_cards IS '问数卡片数（cards）';


--
-- Name: COLUMN object_type.qa30; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.qa30 IS '近 30 天问数次数';


--
-- Name: COLUMN object_type.unused_props; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.unused_props IS '未使用属性数';


--
-- Name: COLUMN object_type.source_table; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.source_table IS '物理表（LINK_OBJECTS.table）';


--
-- Name: COLUMN object_type.datasource_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.datasource_name IS '数据源（Q_OBJECT_TYPES.source）';


--
-- Name: COLUMN object_type.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.owner IS '负责人姓名（软引用）';


--
-- Name: COLUMN object_type.pk_property; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.pk_property IS '主键属性 api（Q_OBJECT_TYPES.pk）';


--
-- Name: COLUMN object_type.record_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.record_count IS '行数（运行时，不进快照）';


--
-- Name: COLUMN object_type.data_cutoff; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.data_cutoff IS '数据截止时间';


--
-- Name: COLUMN object_type.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.seq IS 'mock 顺序';


--
-- Name: COLUMN object_type.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_type.code IS '业务编号（前端 id）';


--
-- Name: object_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.object_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.object_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: object_view; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.object_view (
    code character varying(64) NOT NULL,
    object_type_id bigint NOT NULL,
    name character varying(128) NOT NULL,
    kind character varying(16) NOT NULL,
    owner character varying(64) NOT NULL,
    visibility character varying(16) NOT NULL,
    visible_to character varying(256) NOT NULL,
    last_used_at timestamp with time zone,
    uses_30d integer NOT NULL,
    filter_text text NOT NULL,
    props jsonb NOT NULL,
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_object_view_viewkind CHECK (((kind)::text = ANY ((ARRAY['workbench'::character varying, 'my_page'::character varying, 'object_list'::character varying])::text[]))),
    CONSTRAINT ck_object_view_viewvisibility CHECK (((visibility)::text = ANY ((ARRAY['only_me'::character varying, 'specified'::character varying, 'by_role'::character varying, 'everyone'::character varying])::text[])))
);

ALTER TABLE ONLY public.object_view FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE object_view; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.object_view IS '对象视图';


--
-- Name: COLUMN object_view.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_view.code IS '视图编号（前端 id）';


--
-- Name: COLUMN object_view.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.object_view.owner IS '创建人姓名（软引用）';


--
-- Name: object_view_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.object_view ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.object_view_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: onto_qa_path; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onto_qa_path (
    group_key character varying(160) NOT NULL,
    from_object_id bigint NOT NULL,
    to_object_id bigint NOT NULL,
    seq integer NOT NULL,
    is_default boolean NOT NULL,
    hint character varying(256),
    check_note text,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.onto_qa_path FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE onto_qa_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.onto_qa_path IS '问数路径';


--
-- Name: COLUMN onto_qa_path.group_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.onto_qa_path.group_key IS 'from→to（快照键）[推断]';


--
-- Name: COLUMN onto_qa_path.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.onto_qa_path.seq IS '组内序号（前端 paths 下标）';


--
-- Name: COLUMN onto_qa_path.hint; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.onto_qa_path.hint IS '需指明路径时的提示词';


--
-- Name: COLUMN onto_qa_path.check_note; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.onto_qa_path.check_note IS '组级 V13 校验说明，只存在默认路径行 [推断]';


--
-- Name: onto_qa_path_example; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onto_qa_path_example (
    from_object_id bigint NOT NULL,
    to_object_id bigint NOT NULL,
    seq integer NOT NULL,
    question text NOT NULL,
    path_id bigint,
    how text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.onto_qa_path_example FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE onto_qa_path_example; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.onto_qa_path_example IS '问数路径试问';


--
-- Name: COLUMN onto_qa_path_example.path_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.onto_qa_path_example.path_id IS '空 = 先追问';


--
-- Name: onto_qa_path_example_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.onto_qa_path_example ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.onto_qa_path_example_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: onto_qa_path_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.onto_qa_path ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.onto_qa_path_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: onto_qa_path_step; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.onto_qa_path_step (
    path_id bigint NOT NULL,
    step_no integer NOT NULL,
    link_type_id bigint NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.onto_qa_path_step FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE onto_qa_path_step; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.onto_qa_path_step IS '问数路径步骤';


--
-- Name: COLUMN onto_qa_path_step.step_no; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.onto_qa_path_step.step_no IS '从 0 起';


--
-- Name: onto_qa_path_step_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.onto_qa_path_step ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.onto_qa_path_step_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ontology_edit_rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ontology_edit_rule (
    action_type_id bigint NOT NULL,
    seq integer NOT NULL,
    op character varying(16) NOT NULL,
    obj character varying(128) NOT NULL,
    field text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_ontology_edit_rule_editop CHECK (((op)::text = ANY ((ARRAY['modify'::character varying, 'create'::character varying, 'link'::character varying])::text[])))
);

ALTER TABLE ONLY public.ontology_edit_rule FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE ontology_edit_rule; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ontology_edit_rule IS '本体编辑';


--
-- Name: ontology_edit_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ontology_edit_rule ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ontology_edit_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ontology_member; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ontology_member (
    id bigint NOT NULL,
    ontology_id bigint NOT NULL,
    user_id bigint NOT NULL,
    role character varying(16) NOT NULL,
    CONSTRAINT ck_ontology_member_ontology_member_role CHECK (((role)::text = ANY ((ARRAY['owner'::character varying, 'editor'::character varying, 'viewer'::character varying])::text[])))
);


--
-- Name: ontology_member_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ontology_member ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ontology_member_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ontology_release; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ontology_release (
    id bigint NOT NULL,
    ver character varying(32) NOT NULL,
    parent_id bigint,
    kind character varying(16) DEFAULT 'publish'::character varying NOT NULL,
    rollback_of_id bigint,
    snapshot jsonb NOT NULL,
    snapshot_sha256 character varying(64) NOT NULL,
    change_order_ids character varying(64)[] NOT NULL,
    change_summary jsonb NOT NULL,
    note text NOT NULL,
    published_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    published_by_id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    scope character varying(16) DEFAULT 'legacy'::character varying NOT NULL,
    CONSTRAINT ck_ontology_release_ck_ontology_release_scope CHECK (((scope)::text = ANY ((ARRAY['legacy'::character varying, 'working'::character varying, 'formal'::character varying])::text[]))),
    CONSTRAINT ck_ontology_release_releasekind CHECK (((kind)::text = ANY ((ARRAY['publish'::character varying, 'rollback'::character varying])::text[]))),
    CONSTRAINT ck_ontology_release_ver_format CHECK (((ver)::text ~ '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$'::text))
);

ALTER TABLE ONLY public.ontology_release FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE ontology_release; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ontology_release IS '本体整版发布（只追加）';


--
-- Name: COLUMN ontology_release.ver; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ontology_release.ver IS '版本号 vYYYY.MM.N（业务键，唯一）';


--
-- Name: COLUMN ontology_release.parent_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ontology_release.parent_id IS '上一版（ontology_release.id）';


--
-- Name: COLUMN ontology_release.rollback_of_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ontology_release.rollback_of_id IS '回滚生成的版本：被回滚的版本（ontology_release.id；新版本内容 = 它的上一版）';


--
-- Name: COLUMN ontology_release.snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ontology_release.snapshot IS '整版定义快照（各模块组装，版本层不解析）';


--
-- Name: COLUMN ontology_release.note; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ontology_release.note IS '发布说明（页面显示）';


--
-- Name: COLUMN ontology_release.published_by_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ontology_release.published_by_id IS '发布人（sys_user.id）';


--
-- Name: ontology_release_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ontology_release ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ontology_release_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ontology_role_binding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ontology_role_binding (
    id bigint NOT NULL,
    ontology_id bigint NOT NULL,
    role_id bigint NOT NULL
);


--
-- Name: ontology_role_binding_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ontology_role_binding ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ontology_role_binding_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ontology_workspace; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ontology_workspace (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    domain character varying(64) DEFAULT ''::character varying NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    deleted_at timestamp with time zone,
    workers_paused boolean DEFAULT false NOT NULL
);


--
-- Name: ontology_workspace_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ontology_workspace ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ontology_workspace_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: order_line; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_line (
    order_id bigint NOT NULL,
    line_no integer NOT NULL,
    product_id bigint NOT NULL,
    qty numeric(12,3) NOT NULL,
    price numeric(12,2) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.order_line FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE order_line; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.order_line IS '订单行';


--
-- Name: COLUMN order_line.line_no; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.order_line.line_no IS '行号（mock lines 顺序，从 1 起）';


--
-- Name: COLUMN order_line.qty; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.order_line.qty IS '数量（kg 商品 3 位小数）';


--
-- Name: order_line_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.order_line ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.order_line_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: outbox_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox_event (
    id bigint NOT NULL,
    event_id character varying(200) NOT NULL,
    topic character varying(64) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    available_at timestamp with time zone NOT NULL,
    dispatched_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.outbox_event FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE outbox_event; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.outbox_event IS '事务性 outbox（写方同事务写入，dispatch_outbox 分发）';


--
-- Name: COLUMN outbox_event.event_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.outbox_event.event_id IS '幂等键，订阅方去重';


--
-- Name: outbox_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.outbox_event ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.outbox_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: platform_setting; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.platform_setting (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    value jsonb NOT NULL,
    secret_enc text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL
);


--
-- Name: platform_setting_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.platform_setting ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.platform_setting_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: procrastinate_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_events (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    type public.procrastinate_job_event_type,
    at timestamp with time zone DEFAULT now()
);


--
-- Name: procrastinate_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procrastinate_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procrastinate_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procrastinate_events_id_seq OWNED BY public.procrastinate_events.id;


--
-- Name: procrastinate_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procrastinate_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procrastinate_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procrastinate_jobs_id_seq OWNED BY public.procrastinate_jobs.id;


--
-- Name: procrastinate_periodic_defers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_periodic_defers (
    id bigint NOT NULL,
    task_name character varying(128) NOT NULL,
    defer_timestamp bigint,
    job_id bigint,
    periodic_id character varying(128) DEFAULT ''::character varying NOT NULL
);


--
-- Name: procrastinate_periodic_defers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procrastinate_periodic_defers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procrastinate_periodic_defers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procrastinate_periodic_defers_id_seq OWNED BY public.procrastinate_periodic_defers.id;


--
-- Name: procrastinate_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procrastinate_workers (
    id bigint NOT NULL,
    last_heartbeat timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: procrastinate_workers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.procrastinate_workers ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.procrastinate_workers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: product; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product (
    barcode character varying(32) NOT NULL,
    name character varying(128) NOT NULL,
    spec character varying(64),
    category character varying(64) NOT NULL,
    brand character varying(64),
    price numeric(12,2) NOT NULL,
    unit character varying(16) NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.product FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE product; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.product IS '商品';


--
-- Name: COLUMN product.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.product.category IS '品类路径（生鲜/水果）';


--
-- Name: COLUMN product.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.product.code IS '业务编号（前端 id）';


--
-- Name: product_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.product ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.product_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: property; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.property (
    object_type_id bigint NOT NULL,
    api_name character varying(64) NOT NULL,
    display_name character varying(64) NOT NULL,
    data_type character varying(32) NOT NULL,
    prop_group character varying(32) NOT NULL,
    is_pk boolean NOT NULL,
    is_title boolean NOT NULL,
    is_dimension boolean NOT NULL,
    is_sensitive boolean NOT NULL,
    enum_name character varying(64),
    source_column character varying(256) NOT NULL,
    description text NOT NULL,
    src_type character varying(64) NOT NULL,
    transform text NOT NULL,
    map_column character varying(256) NOT NULL,
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    visibility character varying(16) DEFAULT 'normal'::character varying NOT NULL,
    searchable boolean DEFAULT false NOT NULL,
    filterable boolean DEFAULT false NOT NULL,
    sortable boolean DEFAULT false NOT NULL,
    list_visible boolean DEFAULT false NOT NULL,
    list_width integer,
    format character varying(32),
    unit character varying(16),
    backing_column character varying(64),
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.property FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE property; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.property IS '对象属性';


--
-- Name: COLUMN property.data_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.data_type IS '类型（string/decimal/…，原样）';


--
-- Name: COLUMN property.prop_group; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.prop_group IS '分组（基本/金额/…）';


--
-- Name: COLUMN property.is_dimension; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.is_dimension IS 'mock dim';


--
-- Name: COLUMN property.source_column; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.source_column IS '来源字段（mock source）';


--
-- Name: COLUMN property.src_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.src_type IS '映射页：源类型';


--
-- Name: COLUMN property.transform; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.transform IS '映射页：转换说明';


--
-- Name: COLUMN property.map_column; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.map_column IS '映射页：源字段';


--
-- Name: COLUMN property.visibility; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.visibility IS 'prominent/normal/hidden';


--
-- Name: COLUMN property.list_width; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.list_width IS '列表列宽 px（可选）';


--
-- Name: COLUMN property.format; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.format IS '值格式 money:1/pct/int/raw';


--
-- Name: COLUMN property.unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.unit IS '单位（元/㎡）';


--
-- Name: COLUMN property.backing_column; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.property.backing_column IS '分面取值用：受控业务表（sql_guard 登记）里的物理列名';


--
-- Name: property_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.property ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.property_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_cert; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_cert (
    question character varying(40) NOT NULL,
    sem_plan jsonb NOT NULL,
    metric_code character varying(64),
    metric_version integer,
    status character varying(12) NOT NULL,
    cr_code character varying(32),
    certifier character varying(64),
    certified_at timestamp with time zone,
    hits integer NOT NULL,
    source character varying(20) NOT NULL,
    note text,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_cert_certstatus CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'certified'::character varying, 'recert'::character varying, 'disabled'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_cert FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_cert; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_cert IS '认证问答';


--
-- Name: COLUMN qa_cert.cr_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_cert.cr_code IS '变更单编号软引用';


--
-- Name: COLUMN qa_cert.certifier; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_cert.certifier IS '认证人姓名软引用';


--
-- Name: COLUMN qa_cert.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_cert.code IS '业务编号（前端 id）';


--
-- Name: qa_cert_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_cert ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_cert_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_cert_variant; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_cert_variant (
    cert_id bigint NOT NULL,
    question character varying(40) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_cert_variant FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_cert_variant; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_cert_variant IS '认证问答同问法';


--
-- Name: qa_cert_variant_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_cert_variant ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_cert_variant_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_eval_fail; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_eval_fail (
    run_id bigint NOT NULL,
    question_id bigint NOT NULL,
    actual text NOT NULL,
    kind character varying(24) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_eval_fail_failkind CHECK (((kind)::text = ANY ((ARRAY['metric_not_recalled'::character varying, 'wrong_metric'::character varying, 'dim_mismatch'::character varying, 'time_parse'::character varying, 'missing_filter'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_eval_fail FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_eval_fail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_eval_fail IS '评测失败明细（只追加）';


--
-- Name: COLUMN qa_eval_fail.actual; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_fail.actual IS '[推断] 实际结果文字（mock 是字符串）';


--
-- Name: qa_eval_fail_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_eval_fail ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_eval_fail_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_eval_question; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_eval_question (
    question text NOT NULL,
    group_name character varying(8),
    kind character varying(8) DEFAULT 'semplan'::character varying NOT NULL,
    gold_sql text,
    expect jsonb,
    meta jsonb,
    last_result character varying(8),
    cr_code character varying(32),
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_eval_question_evalkind CHECK (((kind)::text = ANY ((ARRAY['semplan'::character varying, 'sql'::character varying])::text[]))),
    CONSTRAINT ck_qa_eval_question_evallast CHECK (((last_result)::text = ANY ((ARRAY['pass'::character varying, 'fail'::character varying])::text[]))),
    CONSTRAINT ck_qa_eval_question_group_name CHECK (((group_name IS NULL) OR ((group_name)::text = ANY ((ARRAY['销售'::character varying, '退款'::character varying, '履约'::character varying, '毛利'::character varying, '复购'::character varying, '订单'::character varying])::text[])))),
    CONSTRAINT ck_qa_eval_question_sql_has_gold CHECK ((((kind)::text <> 'sql'::text) OR (gold_sql IS NOT NULL)))
);

ALTER TABLE ONLY public.qa_eval_question FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_eval_question; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_eval_question IS '评测题';


--
-- Name: COLUMN qa_eval_question.group_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_question.group_name IS '分组（中文，sql 题为空）';


--
-- Name: COLUMN qa_eval_question.expect; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_question.expect IS '期望 SemPlan（semplan 题）';


--
-- Name: COLUMN qa_eval_question.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_question.meta IS '[推断] sql 题的其余字段（expected/category/tags…）';


--
-- Name: COLUMN qa_eval_question.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_question.code IS '业务编号（前端 id）';


--
-- Name: qa_eval_question_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_eval_question ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_eval_question_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_eval_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_eval_run (
    run_at timestamp with time zone,
    time_label character varying(16),
    trigger_label character varying(40) NOT NULL,
    version_label character varying(32),
    scope character varying(60) NOT NULL,
    total integer NOT NULL,
    passed integer NOT NULL,
    status character varying(8) NOT NULL,
    operator character varying(64) NOT NULL,
    cr_code character varying(32),
    crs character varying(32)[],
    note text,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    release_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    worker_id character varying(32),
    queue_job_id bigint,
    execution_context jsonb,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_eval_run_runstatus CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'done'::character varying, 'failed'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_eval_run FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_eval_run; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_eval_run IS '评测运行';


--
-- Name: COLUMN qa_eval_run.version_label; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_run.version_label IS '[推断] mock version 原文';


--
-- Name: COLUMN qa_eval_run.operator; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_run.operator IS '操作人软引用（含「系统」）';


--
-- Name: COLUMN qa_eval_run.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_run.code IS '业务编号（前端 id）';


--
-- Name: COLUMN qa_eval_run.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_eval_run.release_id IS '本体版本（ontology_release.id）';


--
-- Name: qa_eval_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_eval_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_eval_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_favorite; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_favorite (
    owner_id bigint NOT NULL,
    question text NOT NULL,
    uses integer NOT NULL,
    last_used timestamp with time zone,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_favorite FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_favorite; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_favorite IS '常用问题';


--
-- Name: COLUMN qa_favorite.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_favorite.code IS '业务编号（前端 id）';


--
-- Name: qa_favorite_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_favorite ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_favorite_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_feedback (
    turn_id bigint,
    fed_back_at timestamp with time zone NOT NULL,
    user_id bigint,
    user_name character varying(64) NOT NULL,
    dept character varying(64),
    question text NOT NULL,
    answer text NOT NULL,
    reason text NOT NULL,
    fix_kind character varying(8) NOT NULL,
    fix_text character varying(64) NOT NULL,
    fix_target character varying(64),
    status character varying(16) NOT NULL,
    cr_code character varying(32),
    cert_id bigint,
    handler character varying(64),
    resolved_by character varying(32),
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    cert_variant_id bigint,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_feedback_fbstatus CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'to_synonym'::character varying, 'to_cert'::character varying, 'await_release'::character varying, 'ignored'::character varying])::text[]))),
    CONSTRAINT ck_qa_feedback_fixkind CHECK (((fix_kind)::text = ANY ((ARRAY['synonym'::character varying, 'cert'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_feedback FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_feedback; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_feedback IS '反馈与纠错';


--
-- Name: COLUMN qa_feedback.handler; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_feedback.handler IS '处理人软引用';


--
-- Name: COLUMN qa_feedback.resolved_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_feedback.resolved_by IS 'CR 编号软引用（不是人）';


--
-- Name: COLUMN qa_feedback.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_feedback.code IS '业务编号（前端 id）';


--
-- Name: COLUMN qa_feedback.cert_variant_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_feedback.cert_variant_id IS 'T16 转认证问答时加上的同问法（T17 重开时撤销）';


--
-- Name: qa_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_feedback ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_feedback_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_freq_question; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_freq_question (
    question text NOT NULL,
    hits7 integer NOT NULL,
    hits30 integer NOT NULL,
    users integer NOT NULL,
    sem_plan jsonb NOT NULL,
    consistency numeric(4,3) NOT NULL,
    last_asked timestamp with time zone,
    cert_id bigint,
    computed_at timestamp with time zone,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_freq_question FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_freq_question; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_freq_question IS '高频问题（物化统计）';


--
-- Name: COLUMN qa_freq_question.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_freq_question.code IS '业务编号（前端 id）';


--
-- Name: qa_freq_question_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_freq_question ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_freq_question_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_object_type_view; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_object_type_view (
    kind character varying(16) NOT NULL,
    label character varying(16) NOT NULL,
    unit character varying(8),
    kw_placeholder character varying(64),
    facet_defs jsonb NOT NULL,
    column_defs jsonb NOT NULL,
    field_groups jsonb NOT NULL,
    kpis jsonb NOT NULL,
    quick_questions jsonb NOT NULL,
    sort_order integer NOT NULL,
    id bigint NOT NULL,
    release_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_object_type_view FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_object_type_view; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_object_type_view IS '问数对象类型展示定义（只追加）';


--
-- Name: COLUMN qa_object_type_view.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_object_type_view.release_id IS '本体版本（ontology_release.id）';


--
-- Name: qa_object_type_view_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_object_type_view ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_object_type_view_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_page; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_page (
    owner_id bigint NOT NULL,
    name character varying(30) NOT NULL,
    scope character varying(60),
    source_kind character varying(12) NOT NULL,
    source_ref character varying(200),
    status character varying(16) DEFAULT 'ok'::character varying NOT NULL,
    status_note text,
    refresh jsonb NOT NULL,
    next_run timestamp with time zone,
    last_refresh timestamp with time zone,
    data_as_of timestamp with time zone,
    share_mode character varying(8) DEFAULT 'private'::character varying NOT NULL,
    share_link boolean NOT NULL,
    allow_export boolean NOT NULL,
    link_token character varying(64),
    views integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_page_pagesourcekind CHECK (((source_kind)::text = ANY ((ARRAY['workbench'::character varying, 'template'::character varying, 'manual'::character varying])::text[]))),
    CONSTRAINT ck_qa_page_pagestatus CHECK (((status)::text = ANY ((ARRAY['ok'::character varying, 'refresh_failed'::character varying, 'caliber_pending'::character varying])::text[]))),
    CONSTRAINT ck_qa_page_sharemode CHECK (((share_mode)::text = ANY ((ARRAY['private'::character varying, 'members'::character varying, 'dept'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_page FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_page; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_page IS '我的页面';


--
-- Name: COLUMN qa_page.source_ref; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page.source_ref IS '来源原文（mock source）';


--
-- Name: COLUMN qa_page.refresh; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page.refresh IS 'RefreshPlan（接口值原样存）';


--
-- Name: COLUMN qa_page.views; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page.views IS '[推断] 浏览次数（mock views）';


--
-- Name: COLUMN qa_page.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page.code IS '业务编号（前端 id）';


--
-- Name: qa_page_card; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_page_card (
    page_id bigint NOT NULL,
    card_key character varying(32) NOT NULL,
    sort_order integer NOT NULL,
    title character varying(60) NOT NULL,
    viz character varying(8) NOT NULL,
    sem_plan jsonb,
    metric_name character varying(64),
    metric_code character varying(64),
    metric_version integer,
    pending_cr text,
    source_turn_id bigint,
    result_cache jsonb NOT NULL,
    perm_fingerprint character varying(64),
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_page_card_cardviz CHECK (((viz)::text = ANY ((ARRAY['kpi'::character varying, 'line'::character varying, 'bar'::character varying, 'table'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_page_card FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_page_card; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_page_card IS '页面卡片';


--
-- Name: COLUMN qa_page_card.sem_plan; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_card.sem_plan IS '冻结的语义计划（mock 卡片没有，种子为空）';


--
-- Name: COLUMN qa_page_card.metric_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_card.metric_name IS '指标名快照（mock card.metric）';


--
-- Name: COLUMN qa_page_card.pending_cr; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_card.pending_cr IS '口径待更新说明（mock pending）';


--
-- Name: COLUMN qa_page_card.source_turn_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_card.source_turn_id IS '来源轮次（不加外键）';


--
-- Name: COLUMN qa_page_card.result_cache; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_card.result_cache IS 'value/unit/sub/series/labels/rows';


--
-- Name: qa_page_card_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_page_card ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_page_card_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_page_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_page ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_page_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_page_share_member; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_page_share_member (
    page_id bigint NOT NULL,
    user_id bigint NOT NULL,
    perm character varying(8) NOT NULL,
    dept character varying(64),
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_page_share_member_shareperm CHECK (((perm)::text = ANY ((ARRAY['view'::character varying, 'edit'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_page_share_member FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_page_share_member; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_page_share_member IS '页面共享成员';


--
-- Name: COLUMN qa_page_share_member.dept; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_share_member.dept IS '[推断] 部门快照（ShareMember.dept）';


--
-- Name: qa_page_share_member_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_page_share_member ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_page_share_member_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_page_subscription; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_page_subscription (
    page_id bigint NOT NULL,
    enabled boolean NOT NULL,
    channel character varying(8) NOT NULL,
    recipients character varying(64)[] NOT NULL,
    send_when character varying(16) NOT NULL,
    only_changed boolean NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_page_subscription_subchannel CHECK (((channel)::text = ANY ((ARRAY['email'::character varying, 'wecom'::character varying, 'inbox'::character varying])::text[]))),
    CONSTRAINT ck_qa_page_subscription_subwhen CHECK (((send_when)::text = ANY ((ARRAY['after_refresh'::character varying, 'weekly_mon_0900'::character varying, 'daily_0900'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_page_subscription FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_page_subscription; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_page_subscription IS '页面订阅';


--
-- Name: COLUMN qa_page_subscription.recipients; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_page_subscription.recipients IS '人名软引用';


--
-- Name: qa_page_subscription_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_page_subscription ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_page_subscription_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_permission_request; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_permission_request (
    requester_id bigint NOT NULL,
    scope character varying(60) NOT NULL,
    object_label character varying(60) NOT NULL,
    reason character varying(200) NOT NULL,
    duration_days integer NOT NULL,
    source_turn_id bigint,
    approver character varying(64),
    status character varying(12) NOT NULL,
    decided_at timestamp with time zone,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_permission_request_duration_days CHECK ((duration_days = ANY (ARRAY[30, 90, 180]))),
    CONSTRAINT ck_qa_permission_request_permrequeststatus CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_permission_request FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_permission_request; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_permission_request IS '数据权限申请（W16）';


--
-- Name: COLUMN qa_permission_request.approver; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_permission_request.approver IS '审批人姓名软引用';


--
-- Name: COLUMN qa_permission_request.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_permission_request.code IS '业务编号（前端 id）';


--
-- Name: qa_permission_request_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_permission_request ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_permission_request_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_query_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_query_log (
    turn_id bigint,
    user_id bigint,
    question text NOT NULL,
    norm_question text NOT NULL,
    sem_plan jsonb,
    plan_hash character varying(64),
    metric_code character varying(64),
    metric_version integer,
    cert_id bigint,
    asked_at timestamp with time zone NOT NULL,
    elapsed_ms integer,
    row_count integer,
    kind character varying(12) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_query_log FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_query_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_query_log IS '问数查询日志（只追加）';


--
-- Name: qa_query_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_query_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_query_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_session (
    owner_id bigint NOT NULL,
    title character varying(60) NOT NULL,
    scope character varying(60),
    caliber character varying(32),
    last_at timestamp with time zone NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_session FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_session; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_session IS '问数会话';


--
-- Name: COLUMN qa_session.owner_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_session.owner_id IS '拥有者（sys_user.id）';


--
-- Name: COLUMN qa_session.scope; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_session.scope IS '范围（QA_SCOPE_OPTIONS.value，如 华东）';


--
-- Name: COLUMN qa_session.caliber; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_session.caliber IS '会话口径：指标口径变体标签（如 不含税 / 含税），自由文本，空 = 不限（qa_0004）';


--
-- Name: COLUMN qa_session.last_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_session.last_at IS '最近活动时间（接口 date 只取日期）';


--
-- Name: COLUMN qa_session.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_session.code IS '业务编号（前端 id）';


--
-- Name: qa_session_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_session ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_session_option; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_session_option (
    kind character varying(16) NOT NULL,
    option_value character varying(60) NOT NULL,
    label character varying(60) NOT NULL,
    sort_order integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_session_option_kind CHECK (((kind)::text = ANY ((ARRAY['scope'::character varying, 'caliber'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_session_option FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_session_option; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_session_option IS '问数会话选项（范围 / 口径）';


--
-- Name: qa_session_option_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_session_option ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_session_option_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_synonym_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_synonym_link (
    word character varying(64) NOT NULL,
    target character varying(64) NOT NULL,
    cr_code character varying(32),
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_synonym_link FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_synonym_link; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_synonym_link IS '已走变更单的同义词';


--
-- Name: qa_synonym_link_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_synonym_link ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_synonym_link_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_template; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_template (
    name character varying(60) NOT NULL,
    scene character varying(32) NOT NULL,
    description text NOT NULL,
    provider character varying(64) NOT NULL,
    official boolean NOT NULL,
    uses integer NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_template FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_template; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_template IS '页面模板';


--
-- Name: COLUMN qa_template.provider; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_template.provider IS '提供方软引用';


--
-- Name: COLUMN qa_template.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_template.code IS '业务编号（前端 id）';


--
-- Name: qa_template_card; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_template_card (
    template_id bigint NOT NULL,
    card_key character varying(32) NOT NULL,
    sort_order integer NOT NULL,
    title character varying(60) NOT NULL,
    viz character varying(8) NOT NULL,
    sem_plan jsonb,
    metric_name character varying(64),
    metric_code character varying(64),
    metric_version integer,
    pending_cr text,
    result_cache jsonb NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_template_card_cardviz CHECK (((viz)::text = ANY ((ARRAY['kpi'::character varying, 'line'::character varying, 'bar'::character varying, 'table'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_template_card FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_template_card; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_template_card IS '模板卡片';


--
-- Name: COLUMN qa_template_card.metric_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_template_card.metric_name IS '指标名快照（mock card.metric）';


--
-- Name: qa_template_card_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_template_card ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_template_card_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_template_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_template ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_template_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_turn; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_turn (
    session_id bigint NOT NULL,
    seq integer NOT NULL,
    kind character varying(12) NOT NULL,
    question text,
    refs jsonb,
    sem_plan jsonb,
    snapshot jsonb NOT NULL,
    metric_code character varying(64),
    metric_version integer,
    answered_ver character varying(32),
    sql_text text,
    status character varying(12) DEFAULT 'done'::character varying NOT NULL,
    vote character varying(4) DEFAULT ''::character varying NOT NULL,
    caliber_variant character varying(8),
    asked_at timestamp with time zone,
    elapsed_ms integer,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    release_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_qa_turn_turnkind CHECK (((kind)::text = ANY ((ARRAY['answer'::character varying, 'denied'::character varying, 'empty'::character varying, 'system'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT ck_qa_turn_turnstatus CHECK (((status)::text = ANY ((ARRAY['streaming'::character varying, 'done'::character varying, 'failed'::character varying])::text[])))
);

ALTER TABLE ONLY public.qa_turn FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_turn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_turn IS '问数轮次';


--
-- Name: COLUMN qa_turn.sem_plan; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_turn.sem_plan IS '语义计划 SemPlan';


--
-- Name: COLUMN qa_turn.metric_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_turn.metric_code IS '[推断] 指标软引用（metric 未合入）';


--
-- Name: COLUMN qa_turn.answered_ver; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_turn.answered_ver IS '[推断] 回答时的版本号文字（种子环境 ontology_release 可能为空）';


--
-- Name: COLUMN qa_turn.sql_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_turn.sql_text IS '执行的 SQL（不出接口）';


--
-- Name: COLUMN qa_turn.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_turn.code IS '业务编号（前端 id）';


--
-- Name: COLUMN qa_turn.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.qa_turn.release_id IS '本体版本（ontology_release.id）';


--
-- Name: qa_turn_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.qa_turn_event (
    turn_id bigint NOT NULL,
    seq integer NOT NULL,
    event_name character varying(16) NOT NULL,
    payload jsonb NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.qa_turn_event FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE qa_turn_event; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.qa_turn_event IS '问数轮次事件';


--
-- Name: qa_turn_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_turn_event ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_turn_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qa_turn_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.qa_turn ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.qa_turn_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quality_check; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_check (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    object_type_id bigint NOT NULL,
    name character varying(200) NOT NULL,
    kind character varying(24) NOT NULL,
    threshold character varying(128) NOT NULL,
    limit_value double precision NOT NULL,
    schedule character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    retired_at timestamp with time zone,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    field character varying(64),
    format_rule character varying(24)
);

ALTER TABLE ONLY public.quality_check FORCE ROW LEVEL SECURITY;


--
-- Name: COLUMN quality_check.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.quality_check.code IS '业务编号（前端 id）';


--
-- Name: quality_check_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.quality_check ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.quality_check_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quality_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_run (
    id bigint NOT NULL,
    check_id bigint NOT NULL,
    job_id character varying(64) NOT NULL,
    "time" timestamp with time zone NOT NULL,
    result character varying(24) NOT NULL,
    actual text NOT NULL,
    ms integer NOT NULL,
    data_cutoff timestamp with time zone NOT NULL,
    source_time timestamp with time zone,
    definition jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.quality_run FORCE ROW LEVEL SECURITY;


--
-- Name: quality_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.quality_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.quality_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quality_schedule_cursor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_schedule_cursor (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    check_id bigint NOT NULL,
    check_version integer NOT NULL,
    definition jsonb NOT NULL,
    permission_fingerprint character varying(64) NOT NULL,
    next_at timestamp with time zone,
    last_error character varying(200)
);

ALTER TABLE ONLY public.quality_schedule_cursor FORCE ROW LEVEL SECURITY;


--
-- Name: quality_schedule_cursor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.quality_schedule_cursor ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.quality_schedule_cursor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: reconcile_task; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconcile_task (
    id bigint NOT NULL,
    action_run_id bigint NOT NULL,
    sys_id bigint NOT NULL,
    idem_key character varying(128) NOT NULL,
    state character varying(16) NOT NULL,
    round integer NOT NULL,
    tries integer NOT NULL,
    next_at timestamp with time zone,
    manual boolean NOT NULL,
    last_ext_status character varying(32),
    last_error character varying(128),
    resolved_at timestamp with time zone,
    reconciler character varying(64),
    reconcile_result character varying(16),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_reconcile_task_reconcilestate CHECK (((state)::text = ANY ((ARRAY['querying'::character varying, 'resolved'::character varying, 'manual'::character varying])::text[])))
);

ALTER TABLE ONLY public.reconcile_task FORCE ROW LEVEL SECURITY;


--
-- Name: reconcile_task_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.reconcile_task ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.reconcile_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rescue_incident_w4_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rescue_incident_w4_demo (
    id bigint NOT NULL,
    ontology_id bigint DEFAULT 4 NOT NULL,
    incident_id character varying(64) NOT NULL,
    code character varying(64) GENERATED ALWAYS AS (incident_id) STORED,
    status character varying(32) NOT NULL,
    distress_position character varying(128),
    team_id character varying(64),
    lock_version integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rescue_incident_w4_demo_ontology_id_check CHECK ((ontology_id = 4)),
    CONSTRAINT rescue_incident_w4_demo_status_check CHECK (((status)::text = ANY ((ARRAY['待响应'::character varying, '搜救中'::character varying, '已结束'::character varying])::text[])))
);

ALTER TABLE ONLY public.rescue_incident_w4_demo FORCE ROW LEVEL SECURITY;


--
-- Name: rescue_incident_w4_demo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rescue_incident_w4_demo ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.rescue_incident_w4_demo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rescue_team_w4_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rescue_team_w4_demo (
    id bigint NOT NULL,
    ontology_id bigint DEFAULT 4 NOT NULL,
    team_id character varying(64) NOT NULL,
    code character varying(64) GENERATED ALWAYS AS (team_id) STORED,
    status character varying(32) NOT NULL,
    CONSTRAINT rescue_team_w4_demo_ontology_id_check CHECK ((ontology_id = 4))
);

ALTER TABLE ONLY public.rescue_team_w4_demo FORCE ROW LEVEL SECURITY;


--
-- Name: rescue_team_w4_demo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rescue_team_w4_demo ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.rescue_team_w4_demo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule (
    version integer,
    name character varying(200) NOT NULL,
    "desc" text NOT NULL,
    owner character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    core boolean DEFAULT false NOT NULL,
    obj_kind character varying(32) NOT NULL,
    level character varying(16) NOT NULL,
    output character varying(16) NOT NULL,
    conds jsonb NOT NULL,
    logic character varying(8) NOT NULL,
    expr text NOT NULL,
    dedupe character varying(200) NOT NULL,
    cooldown character varying(64) NOT NULL,
    trigger jsonb NOT NULL,
    action_type_id bigint,
    auto_stage jsonb NOT NULL,
    weekly integer NOT NULL,
    confirmer character varying(64),
    published_by character varying(64),
    published_at timestamp with time zone,
    change_note text,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    release_id bigint,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_rule_alertlevel CHECK (((level)::text = ANY ((ARRAY['high'::character varying, 'medium'::character varying, 'low'::character varying])::text[]))),
    CONSTRAINT ck_rule_ruleoutput CHECK (((output)::text = ANY ((ARRAY['alert'::character varying, 'notify'::character varying])::text[]))),
    CONSTRAINT ck_rule_rulestatus CHECK (((status)::text = ANY ((ARRAY['published'::character varying, 'changing'::character varying, 'disabled'::character varying])::text[])))
);

ALTER TABLE ONLY public.rule FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE rule; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rule IS '规则（当前定义；RULES 5 条核心 + RULE_DEFS 16 条合并）';


--
-- Name: COLUMN rule.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.version IS '当前生效版本；null = 回滚后不在快照里';


--
-- Name: COLUMN rule.conds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.conds IS '[{metric,metricVer,field,op,value}]';


--
-- Name: COLUMN rule.trigger; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.trigger IS '{kind,cron,text,eventId,filter}';


--
-- Name: COLUMN rule.auto_stage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.auto_stage IS '{enabled,actionTypeId,confirmer,mapping[{param,from}]}';


--
-- Name: COLUMN rule.weekly; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.weekly IS '周命中数（mock weekly）';


--
-- Name: COLUMN rule.confirmer; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.confirmer IS '固定确认人（RULE_CONFIRMER）';


--
-- Name: COLUMN rule.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.code IS '业务编号（前端 id）';


--
-- Name: COLUMN rule.release_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.release_id IS '本体版本（ontology_release.id）';


--
-- Name: rule_draft; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_draft (
    rule_id bigint NOT NULL,
    cr_code character varying(32) NOT NULL,
    version integer NOT NULL,
    patch jsonb NOT NULL,
    by character varying(64),
    at timestamp with time zone,
    change text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.rule_draft FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE rule_draft; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rule_draft IS '规则草稿改动（暂代 cr_item）';


--
-- Name: COLUMN rule_draft.cr_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule_draft.cr_code IS '变更单号（软引用，FK to add later）';


--
-- Name: COLUMN rule_draft.patch; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule_draft.patch IS 'RULE_DRAFTS 的改动（Partial<RuleDef>）';


--
-- Name: COLUMN rule_draft.change; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule_draft.change IS '版本历史里草稿行的说明';


--
-- Name: rule_draft_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rule_draft ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rule_draft_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rule_hit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_hit (
    rule_id bigint NOT NULL,
    rule_version integer,
    seq integer NOT NULL,
    at timestamp with time zone NOT NULL,
    obj_kind character varying(32) NOT NULL,
    obj_id character varying(64) NOT NULL,
    obj_label character varying(200) NOT NULL,
    value character varying(200) NOT NULL,
    threshold character varying(200) NOT NULL,
    dedupe_key character varying(200),
    result character varying(16) NOT NULL,
    alert_id bigint,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    dims jsonb NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_rule_hit_hitresult CHECK (((result)::text = ANY ((ARRAY['alert'::character varying, 'notified'::character varying, 'deduped'::character varying])::text[])))
);

ALTER TABLE ONLY public.rule_hit FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE rule_hit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rule_hit IS '规则命中记录（只追加）';


--
-- Name: COLUMN rule_hit.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule_hit.seq IS '同一规则内 hitsOf 顺序（at 倒序，同秒按 mock 顺序）';


--
-- Name: COLUMN rule_hit.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule_hit.code IS '业务编号（前端 id）';


--
-- Name: COLUMN rule_hit.dims; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule_hit.dims IS '通用维度值 [{objKind,id,label}]（同 alert.dims）';


--
-- Name: rule_hit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rule_hit ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rule_hit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rule_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rule ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rule_schedule_cursor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_schedule_cursor (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    rule_id bigint NOT NULL,
    rule_version integer NOT NULL,
    next_at timestamp with time zone NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.rule_schedule_cursor FORCE ROW LEVEL SECURITY;


--
-- Name: rule_schedule_cursor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rule_schedule_cursor ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rule_schedule_cursor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rule_schedule_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_schedule_run (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    rule_id bigint NOT NULL,
    rule_version integer NOT NULL,
    release_id bigint,
    scheduled_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    state character varying(20) NOT NULL,
    definition jsonb NOT NULL,
    permission_fingerprint character varying(64) NOT NULL,
    result jsonb,
    error text,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.rule_schedule_run FORCE ROW LEVEL SECURITY;


--
-- Name: rule_schedule_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rule_schedule_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rule_schedule_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rule_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_version (
    rule_id bigint NOT NULL,
    version integer NOT NULL,
    by character varying(64) NOT NULL,
    at timestamp with time zone NOT NULL,
    change text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.rule_version FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE rule_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rule_version IS '规则版本历史（只追加）';


--
-- Name: rule_version_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rule_version ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rule_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: scenario; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scenario (
    name character varying(200) NOT NULL,
    owner character varying(64) NOT NULL,
    created_on timestamp with time zone NOT NULL,
    base_release_id bigint,
    status character varying(16) NOT NULL,
    scope jsonb NOT NULL,
    drivers jsonb NOT NULL,
    fail_reason text,
    staged character varying(64)[] NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    template_code character varying(64) NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_scenario_scenariostatus CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'running'::character varying, 'done'::character varying, 'failed'::character varying])::text[])))
);

ALTER TABLE ONLY public.scenario FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE scenario; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.scenario IS '情景推演方案';


--
-- Name: COLUMN scenario.created_on; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario.created_on IS 'mock createdAt';


--
-- Name: COLUMN scenario.staged; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario.staged IS '已转为暂存动作的动作编号（action_run.code）';


--
-- Name: COLUMN scenario.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario.code IS '业务编号（前端 id）';


--
-- Name: COLUMN scenario.template_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario.template_code IS '推演模板编号（SC_TEMPLATES.code）';


--
-- Name: scenario_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.scenario ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.scenario_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: scenario_impact; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scenario_impact (
    scenario_id bigint NOT NULL,
    seq integer NOT NULL,
    sku character varying(64) NOT NULL,
    name character varying(200) NOT NULL,
    category character varying(64) NOT NULL,
    store_id character varying(64) NOT NULL,
    store_name character varying(200) NOT NULL,
    base_sales bigint NOT NULL,
    sales bigint NOT NULL,
    stockout_base double precision NOT NULL,
    stockout double precision NOT NULL,
    suggest character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.scenario_impact FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE scenario_impact; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.scenario_impact IS '推演影响明细（SKU × 门店）';


--
-- Name: COLUMN scenario_impact.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario_impact.seq IS '结果里的顺序（按影响绝对值降序）';


--
-- Name: scenario_impact_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.scenario_impact ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.scenario_impact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: scenario_result; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scenario_result (
    scenario_id bigint NOT NULL,
    ran_at timestamp with time zone NOT NULL,
    scope jsonb NOT NULL,
    drivers jsonb NOT NULL,
    weeks jsonb NOT NULL,
    base_sales jsonb NOT NULL,
    sales jsonb NOT NULL,
    base_margin jsonb NOT NULL,
    margin jsonb NOT NULL,
    kpis jsonb NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL
);

ALTER TABLE ONLY public.scenario_result FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE scenario_result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.scenario_result IS '推演结果（每个方案最近一次）';


--
-- Name: scenario_result_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.scenario_result ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.scenario_result_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: scenario_template; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scenario_template (
    id bigint NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(200) NOT NULL,
    engine character varying(64) NOT NULL,
    object_type character varying(64) NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    definition jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL
);

ALTER TABLE ONLY public.scenario_template FORCE ROW LEVEL SECURITY;


--
-- Name: COLUMN scenario_template.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario_template.code IS '业务编号（前端 id）';


--
-- Name: COLUMN scenario_template.engine; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario_template.engine IS '执行器键（ENGINES）';


--
-- Name: COLUMN scenario_template.object_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario_template.object_type IS '目标对象类型 api_name';


--
-- Name: COLUMN scenario_template.definition; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.scenario_template.definition IS '模板定义（SC_TEMPLATES 单条）';


--
-- Name: scenario_template_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.scenario_template ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.scenario_template_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_account; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_account (
    id bigint NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(64) NOT NULL,
    description character varying(512) DEFAULT ''::character varying NOT NULL,
    role_id bigint NOT NULL,
    creator character varying(128) NOT NULL,
    revoked_at timestamp with time zone,
    revoked_by character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL
);


--
-- Name: TABLE service_account; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.service_account IS '服务账号';


--
-- Name: COLUMN service_account.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.service_account.code IS '业务编号（前端 id）';


--
-- Name: service_account_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_account ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_account_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: side_effect; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.side_effect (
    action_type_id bigint NOT NULL,
    seq integer NOT NULL,
    kind character varying(16) NOT NULL,
    target character varying(255) NOT NULL,
    when_text text NOT NULL,
    note text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_side_effect_effectkind CHECK (((kind)::text = ANY ((ARRAY['notify'::character varying, 'event'::character varying, 'webhook'::character varying])::text[])))
);

ALTER TABLE ONLY public.side_effect FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE side_effect; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.side_effect IS '副作用';


--
-- Name: COLUMN side_effect.when_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.side_effect.when_text IS '接口 when';


--
-- Name: side_effect_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.side_effect ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.side_effect_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: store; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.store (
    store_no character varying(32) NOT NULL,
    name character varying(128) NOT NULL,
    city character varying(32) NOT NULL,
    region character varying(32) NOT NULL,
    big_region character varying(32) NOT NULL,
    format character varying(32) NOT NULL,
    manager character varying(64),
    area numeric(10,1),
    opened_at date NOT NULL,
    status character varying(16) NOT NULL,
    is_test boolean DEFAULT false NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_store_storestatus CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'renovating'::character varying, 'closed'::character varying])::text[])))
);

ALTER TABLE ONLY public.store FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE store; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.store IS '门店';


--
-- Name: COLUMN store.store_no; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.store_no IS '门店编码（SH-PD-0031）';


--
-- Name: COLUMN store.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.region IS '区域（华东一区）';


--
-- Name: COLUMN store.big_region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.big_region IS '[推断] 大区，由区域推出（华东一区 → 华东）';


--
-- Name: COLUMN store.format; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.format IS '业态（社区店/商场店/写字楼店）';


--
-- Name: COLUMN store.manager; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.manager IS '店长姓名（mock 原文，不是用户外键）';


--
-- Name: COLUMN store.area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.area IS '面积 ㎡（mock 有 6 店为空）';


--
-- Name: COLUMN store.is_test; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.is_test IS '[推断] 是否测试店（metricCatalog.ts:33 gmv 过滤 门店.是否测试店；mock 无数据，seed 全 false）';


--
-- Name: COLUMN store.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.store.code IS '业务编号（前端 id）';


--
-- Name: store_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.store ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.store_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: submission_criterion; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_criterion (
    action_type_id bigint NOT NULL,
    seq integer NOT NULL,
    kind character varying(16) NOT NULL,
    name character varying(64) NOT NULL,
    expr_text text NOT NULL,
    expr_ast jsonb,
    fail_msg text NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_submission_criterion_criterionkind CHECK (((kind)::text = ANY ((ARRAY['current_user'::character varying, 'parameter'::character varying, 'context_object'::character varying])::text[])))
);

ALTER TABLE ONLY public.submission_criterion FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE submission_criterion; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_criterion IS '提交条件';


--
-- Name: COLUMN submission_criterion.expr_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.submission_criterion.expr_text IS '接口 expr';


--
-- Name: COLUMN submission_criterion.fail_msg; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.submission_criterion.fail_msg IS '接口 fail';


--
-- Name: submission_criterion_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.submission_criterion ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.submission_criterion_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sys_org; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sys_org (
    name character varying(64) NOT NULL,
    seq integer DEFAULT 0 NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL
);


--
-- Name: TABLE sys_org; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.sys_org IS '组织（顶栏切换）';


--
-- Name: COLUMN sys_org.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_org.name IS '组织名';


--
-- Name: COLUMN sys_org.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_org.seq IS '显示顺序';


--
-- Name: COLUMN sys_org.is_default; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_org.is_default IS '默认组织';


--
-- Name: COLUMN sys_org.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_org.code IS '业务编号（前端 id）';


--
-- Name: sys_org_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sys_org ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sys_org_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sys_role; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sys_role (
    name character varying(64) NOT NULL,
    description character varying(512) DEFAULT ''::character varying NOT NULL,
    built_in boolean DEFAULT false NOT NULL,
    default_deny boolean DEFAULT false NOT NULL,
    func jsonb NOT NULL,
    objs jsonb NOT NULL,
    changed_at timestamp with time zone NOT NULL,
    changed_by character varying(64) NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL
);


--
-- Name: TABLE sys_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.sys_role IS '角色';


--
-- Name: COLUMN sys_role.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.name IS '角色名（唯一）';


--
-- Name: COLUMN sys_role.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.description IS '说明';


--
-- Name: COLUMN sys_role.built_in; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.built_in IS '内置角色（只读、不能删）';


--
-- Name: COLUMN sys_role.default_deny; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.default_deny IS '未配范围的对象类型默认不可见';


--
-- Name: COLUMN sys_role.func; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.func IS '功能权限 {pageKey: none|view|manage}';


--
-- Name: COLUMN sys_role.objs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.objs IS '对象与动作权限 ObjPerm[]';


--
-- Name: COLUMN sys_role.changed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.changed_at IS '前端 updatedAt';


--
-- Name: COLUMN sys_role.changed_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.changed_by IS '前端 updatedBy（姓名）';


--
-- Name: COLUMN sys_role.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role.code IS '业务编号（前端 id）';


--
-- Name: sys_role_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sys_role ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sys_role_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sys_role_member; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sys_role_member (
    role_id bigint NOT NULL,
    user_id bigint NOT NULL,
    seq integer NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL
);


--
-- Name: TABLE sys_role_member; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.sys_role_member IS '角色成员';


--
-- Name: COLUMN sys_role_member.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_member.seq IS '列表顺序（升序）';


--
-- Name: sys_role_member_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sys_role_member ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sys_role_member_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sys_role_scope; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sys_role_scope (
    role_id bigint NOT NULL,
    seq integer NOT NULL,
    obj_type character varying(64) NOT NULL,
    prop character varying(64) NOT NULL,
    op character varying(4) NOT NULL,
    "values" jsonb NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    is_seed boolean DEFAULT false NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    object_type_id bigint,
    object_type_code character varying(64)
);

ALTER TABLE ONLY public.sys_role_scope FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE sys_role_scope; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.sys_role_scope IS '角色行级范围';


--
-- Name: COLUMN sys_role_scope.seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.seq IS '角色内顺序';


--
-- Name: COLUMN sys_role_scope.obj_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.obj_type IS '对象类型中文名（订单）';


--
-- Name: COLUMN sys_role_scope.prop; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.prop IS '权限依据属性（大区）';


--
-- Name: COLUMN sys_role_scope.op; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.op IS '= 或 ∈';


--
-- Name: COLUMN sys_role_scope."values"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope."values" IS '取值 string[]';


--
-- Name: COLUMN sys_role_scope.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.code IS '业务编号（前端 id）';


--
-- Name: COLUMN sys_role_scope.object_type_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.object_type_id IS '对象类型（稳定引用）';


--
-- Name: COLUMN sys_role_scope.object_type_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_role_scope.object_type_code IS '对象类型编号（写入时快照）';


--
-- Name: sys_role_scope_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sys_role_scope ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sys_role_scope_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sys_tenant; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sys_tenant (
    name character varying(128) NOT NULL,
    short_name character varying(32) NOT NULL,
    industry character varying(64) DEFAULT ''::character varying NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL
);


--
-- Name: TABLE sys_tenant; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.sys_tenant IS '租户';


--
-- Name: COLUMN sys_tenant.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_tenant.name IS '租户全称';


--
-- Name: COLUMN sys_tenant.short_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_tenant.short_name IS '简称（顶栏、导出抬头）';


--
-- Name: COLUMN sys_tenant.industry; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_tenant.industry IS '行业';


--
-- Name: COLUMN sys_tenant.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_tenant.code IS '业务编号（前端 id）';


--
-- Name: sys_tenant_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sys_tenant ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sys_tenant_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sys_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sys_user (
    name character varying(64) NOT NULL,
    account character varying(128),
    dept character varying(128),
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    last_login_at timestamp with time zone,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    alert_assignable smallint,
    role character varying(64),
    joined_on date,
    CONSTRAINT ck_sys_user_alert_assignable_positive CHECK ((alert_assignable >= 1)),
    CONSTRAINT ck_sys_user_userstatus CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'disabled'::character varying, 'pending'::character varying])::text[])))
);


--
-- Name: TABLE sys_user; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.sys_user IS '用户（最小占位，PDE 补全）';


--
-- Name: COLUMN sys_user.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.name IS '姓名（不唯一）';


--
-- Name: COLUMN sys_user.account; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.account IS '登录账号（邮箱）';


--
-- Name: COLUMN sys_user.dept; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.dept IS '部门（mock 原文，如「华东事业部 / 运营中心」）';


--
-- Name: COLUMN sys_user.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.code IS '业务编号（前端 id）';


--
-- Name: COLUMN sys_user.alert_assignable; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.alert_assignable IS '告警可指派：动态 PEOPLE 列表里的顺序（1..7），null = 不可指派；由动态 seed 按姓名回填';


--
-- Name: COLUMN sys_user.role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.role IS '角色名（mock AdminUser.role）；null = 不在用户列表里的占位人（SYS_USERS）';


--
-- Name: COLUMN sys_user.joined_on; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.sys_user.joined_on IS '创建日期（前端 createdAt，YYYY-MM-DD）';


--
-- Name: sys_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sys_user ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sys_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: writeback_call; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.writeback_call (
    sys_id bigint NOT NULL,
    action_run_id bigint,
    kind character varying(16) NOT NULL,
    at timestamp with time zone NOT NULL,
    op_name character varying(64) NOT NULL,
    target_label character varying(200) NOT NULL,
    method character varying(8) NOT NULL,
    path character varying(255) NOT NULL,
    idem_key character varying(128),
    elapsed_ms integer NOT NULL,
    http_status integer,
    result character varying(16) NOT NULL,
    attempt integer NOT NULL,
    actor character varying(128) NOT NULL,
    msg text NOT NULL,
    code character varying(64) NOT NULL,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    lock_version integer DEFAULT 1 NOT NULL,
    ontology_id bigint DEFAULT (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint NOT NULL,
    CONSTRAINT ck_writeback_call_callkind CHECK (((kind)::text = ANY ((ARRAY['write'::character varying, 'query'::character varying, 'test'::character varying])::text[]))),
    CONSTRAINT ck_writeback_call_callresult CHECK (((result)::text = ANY ((ARRAY['succeeded'::character varying, 'failed'::character varying, 'unknown'::character varying])::text[])))
);

ALTER TABLE ONLY public.writeback_call FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE writeback_call; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.writeback_call IS '外部系统调用记录（integration 模块）';


--
-- Name: COLUMN writeback_call.action_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.action_run_id IS '执行记录（接口 runId）';


--
-- Name: COLUMN writeback_call.at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.at IS '调用时间（接口 time）';


--
-- Name: COLUMN writeback_call.op_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.op_name IS '接口 action';


--
-- Name: COLUMN writeback_call.target_label; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.target_label IS '接口 target';


--
-- Name: COLUMN writeback_call.idem_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.idem_key IS '幂等键；不支持幂等为空';


--
-- Name: COLUMN writeback_call.elapsed_ms; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.elapsed_ms IS '接口 ms';


--
-- Name: COLUMN writeback_call.http_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.http_status IS '接口 http；超时为空';


--
-- Name: COLUMN writeback_call.actor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.actor IS '发起人原文';


--
-- Name: COLUMN writeback_call.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.writeback_call.code IS '业务编号（前端 id）';


--
-- Name: writeback_call_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.writeback_call ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.writeback_call_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: procrastinate_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_events ALTER COLUMN id SET DEFAULT nextval('public.procrastinate_events_id_seq'::regclass);


--
-- Name: procrastinate_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_jobs ALTER COLUMN id SET DEFAULT nextval('public.procrastinate_jobs_id_seq'::regclass);


--
-- Name: procrastinate_periodic_defers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers ALTER COLUMN id SET DEFAULT nextval('public.procrastinate_periodic_defers_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: maintenance_asset_demo_20261028 maintenance_asset_demo_20261028_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_asset_demo_20261028
    ADD CONSTRAINT maintenance_asset_demo_20261028_pkey PRIMARY KEY (id);


--
-- Name: maintenance_asset_extra_demo_20261028 maintenance_asset_extra_demo_20261028_ontology_id_asset_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_asset_extra_demo_20261028
    ADD CONSTRAINT maintenance_asset_extra_demo_20261028_ontology_id_asset_id_key UNIQUE (ontology_id, asset_id);


--
-- Name: maintenance_asset_extra_demo_20261028 maintenance_asset_extra_demo_20261028_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_asset_extra_demo_20261028
    ADD CONSTRAINT maintenance_asset_extra_demo_20261028_pkey PRIMARY KEY (id);


--
-- Name: maintenance_quality_demo_20261028 maintenance_quality_demo_20261028_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_quality_demo_20261028
    ADD CONSTRAINT maintenance_quality_demo_20261028_pkey PRIMARY KEY (id);


--
-- Name: maintenance_task_demo_20261028 maintenance_task_demo_20261028_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_task_demo_20261028
    ADD CONSTRAINT maintenance_task_demo_20261028_pkey PRIMARY KEY (id);


--
-- Name: action_param pk_action_param; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_param
    ADD CONSTRAINT pk_action_param PRIMARY KEY (id);


--
-- Name: action_run pk_action_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT pk_action_run PRIMARY KEY (id);


--
-- Name: action_run_daily_counter pk_action_run_daily_counter; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_counter
    ADD CONSTRAINT pk_action_run_daily_counter PRIMARY KEY (id);


--
-- Name: action_run_daily_stat pk_action_run_daily_stat; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_stat
    ADD CONSTRAINT pk_action_run_daily_stat PRIMARY KEY (id);


--
-- Name: action_run_log pk_action_run_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_log
    ADD CONSTRAINT pk_action_run_log PRIMARY KEY (id);


--
-- Name: action_type pk_action_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type
    ADD CONSTRAINT pk_action_type PRIMARY KEY (id);


--
-- Name: action_type_rule pk_action_type_rule; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_rule
    ADD CONSTRAINT pk_action_type_rule PRIMARY KEY (id);


--
-- Name: action_type_version pk_action_type_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_version
    ADD CONSTRAINT pk_action_type_version PRIMARY KEY (id);


--
-- Name: alert pk_alert; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert
    ADD CONSTRAINT pk_alert PRIMARY KEY (id);


--
-- Name: alert_log pk_alert_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_log
    ADD CONSTRAINT pk_alert_log PRIMARY KEY (id);


--
-- Name: api_key pk_api_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT pk_api_key PRIMARY KEY (id);


--
-- Name: api_key_call pk_api_key_call; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_call
    ADD CONSTRAINT pk_api_key_call PRIMARY KEY (id);


--
-- Name: api_key_usage pk_api_key_usage; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_usage
    ADD CONSTRAINT pk_api_key_usage PRIMARY KEY (id);


--
-- Name: audit_export_job pk_audit_export_job; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_job
    ADD CONSTRAINT pk_audit_export_job PRIMARY KEY (id);


--
-- Name: audit_export_row pk_audit_export_row; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_row
    ADD CONSTRAINT pk_audit_export_row PRIMARY KEY (id);


--
-- Name: audit_log pk_audit_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT pk_audit_log PRIMARY KEY (id);


--
-- Name: biz_order pk_biz_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_order
    ADD CONSTRAINT pk_biz_order PRIMARY KEY (id);


--
-- Name: change_request pk_change_request; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_request
    ADD CONSTRAINT pk_change_request PRIMARY KEY (id);


--
-- Name: cr_check pk_cr_check; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_check
    ADD CONSTRAINT pk_cr_check PRIMARY KEY (id);


--
-- Name: cr_event pk_cr_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_event
    ADD CONSTRAINT pk_cr_event PRIMARY KEY (id);


--
-- Name: cr_impact pk_cr_impact; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_impact
    ADD CONSTRAINT pk_cr_impact PRIMARY KEY (id);


--
-- Name: cr_item pk_cr_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_item
    ADD CONSTRAINT pk_cr_item PRIMARY KEY (id);


--
-- Name: cr_target pk_cr_target; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_target
    ADD CONSTRAINT pk_cr_target PRIMARY KEY (id);


--
-- Name: cr_trial pk_cr_trial; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_trial
    ADD CONSTRAINT pk_cr_trial PRIMARY KEY (id);


--
-- Name: datasource pk_datasource; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource
    ADD CONSTRAINT pk_datasource PRIMARY KEY (id);


--
-- Name: datasource_run pk_datasource_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_run
    ADD CONSTRAINT pk_datasource_run PRIMARY KEY (id);


--
-- Name: datasource_table pk_datasource_table; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_table
    ADD CONSTRAINT pk_datasource_table PRIMARY KEY (id);


--
-- Name: decision_log pk_decision_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_log
    ADD CONSTRAINT pk_decision_log PRIMARY KEY (id);


--
-- Name: dim_level pk_dim_level; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_level
    ADD CONSTRAINT pk_dim_level PRIMARY KEY (id);


--
-- Name: dim_mapping pk_dim_mapping; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_mapping
    ADD CONSTRAINT pk_dim_mapping PRIMARY KEY (id);


--
-- Name: dim_value pk_dim_value; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_value
    ADD CONSTRAINT pk_dim_value PRIMARY KEY (id);


--
-- Name: dimension pk_dimension; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dimension
    ADD CONSTRAINT pk_dimension PRIMARY KEY (id);


--
-- Name: event pk_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT pk_event PRIMARY KEY (id);


--
-- Name: event_daily_stat pk_event_daily_stat; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_daily_stat
    ADD CONSTRAINT pk_event_daily_stat PRIMARY KEY (id);


--
-- Name: event_dedup pk_event_dedup; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_dedup
    ADD CONSTRAINT pk_event_dedup PRIMARY KEY (id);


--
-- Name: event_delivery pk_event_delivery; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery
    ADD CONSTRAINT pk_event_delivery PRIMARY KEY (id);


--
-- Name: event_delivery_record pk_event_delivery_record; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery_record
    ADD CONSTRAINT pk_event_delivery_record PRIMARY KEY (id);


--
-- Name: event_envelope_field pk_event_envelope_field; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_envelope_field
    ADD CONSTRAINT pk_event_envelope_field PRIMARY KEY (id);


--
-- Name: event_payload_field pk_event_payload_field; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_payload_field
    ADD CONSTRAINT pk_event_payload_field PRIMARY KEY (id);


--
-- Name: event_subscription pk_event_subscription; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_subscription
    ADD CONSTRAINT pk_event_subscription PRIMARY KEY (id);


--
-- Name: event_type pk_event_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type
    ADD CONSTRAINT pk_event_type PRIMARY KEY (id);


--
-- Name: event_type_version pk_event_type_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type_version
    ADD CONSTRAINT pk_event_type_version PRIMARY KEY (id);


--
-- Name: ext_system pk_ext_system; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_system
    ADD CONSTRAINT pk_ext_system PRIMARY KEY (id);


--
-- Name: fn_param pk_fn_param; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_param
    ADD CONSTRAINT pk_fn_param PRIMARY KEY (id);


--
-- Name: fn_use pk_fn_use; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_use
    ADD CONSTRAINT pk_fn_use PRIMARY KEY (id);


--
-- Name: fn_version pk_fn_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_version
    ADD CONSTRAINT pk_fn_version PRIMARY KEY (id);


--
-- Name: function pk_function; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.function
    ADD CONSTRAINT pk_function PRIMARY KEY (id);


--
-- Name: governed_binding pk_governed_binding; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governed_binding
    ADD CONSTRAINT pk_governed_binding PRIMARY KEY (id);


--
-- Name: knowledge pk_knowledge; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge
    ADD CONSTRAINT pk_knowledge PRIMARY KEY (id);


--
-- Name: knowledge_apply pk_knowledge_apply; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_apply
    ADD CONSTRAINT pk_knowledge_apply PRIMARY KEY (id);


--
-- Name: lifecycle pk_lifecycle; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle
    ADD CONSTRAINT pk_lifecycle PRIMARY KEY (id);


--
-- Name: lifecycle_change pk_lifecycle_change; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_change
    ADD CONSTRAINT pk_lifecycle_change PRIMARY KEY (id);


--
-- Name: lifecycle_draft pk_lifecycle_draft; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_draft
    ADD CONSTRAINT pk_lifecycle_draft PRIMARY KEY (id);


--
-- Name: lifecycle_state pk_lifecycle_state; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_state
    ADD CONSTRAINT pk_lifecycle_state PRIMARY KEY (id);


--
-- Name: lifecycle_transition pk_lifecycle_transition; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT pk_lifecycle_transition PRIMARY KEY (id);


--
-- Name: lifecycle_version pk_lifecycle_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_version
    ADD CONSTRAINT pk_lifecycle_version PRIMARY KEY (id);


--
-- Name: link_quality pk_link_quality; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_quality
    ADD CONSTRAINT pk_link_quality PRIMARY KEY (id);


--
-- Name: link_type pk_link_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_type
    ADD CONSTRAINT pk_link_type PRIMARY KEY (id);


--
-- Name: metric pk_metric; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric
    ADD CONSTRAINT pk_metric PRIMARY KEY (id);


--
-- Name: notification pk_notification; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT pk_notification PRIMARY KEY (id);


--
-- Name: object_type pk_object_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_type
    ADD CONSTRAINT pk_object_type PRIMARY KEY (id);


--
-- Name: object_view pk_object_view; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_view
    ADD CONSTRAINT pk_object_view PRIMARY KEY (id);


--
-- Name: onto_qa_path pk_onto_qa_path; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path
    ADD CONSTRAINT pk_onto_qa_path PRIMARY KEY (id);


--
-- Name: onto_qa_path_example pk_onto_qa_path_example; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_example
    ADD CONSTRAINT pk_onto_qa_path_example PRIMARY KEY (id);


--
-- Name: onto_qa_path_step pk_onto_qa_path_step; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_step
    ADD CONSTRAINT pk_onto_qa_path_step PRIMARY KEY (id);


--
-- Name: ontology_edit_rule pk_ontology_edit_rule; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_edit_rule
    ADD CONSTRAINT pk_ontology_edit_rule PRIMARY KEY (id);


--
-- Name: ontology_member pk_ontology_member; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_member
    ADD CONSTRAINT pk_ontology_member PRIMARY KEY (id);


--
-- Name: ontology_release pk_ontology_release; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_release
    ADD CONSTRAINT pk_ontology_release PRIMARY KEY (id);


--
-- Name: ontology_role_binding pk_ontology_role_binding; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_role_binding
    ADD CONSTRAINT pk_ontology_role_binding PRIMARY KEY (id);


--
-- Name: ontology_workspace pk_ontology_workspace; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_workspace
    ADD CONSTRAINT pk_ontology_workspace PRIMARY KEY (id);


--
-- Name: order_line pk_order_line; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_line
    ADD CONSTRAINT pk_order_line PRIMARY KEY (id);


--
-- Name: outbox_event pk_outbox_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_event
    ADD CONSTRAINT pk_outbox_event PRIMARY KEY (id);


--
-- Name: platform_setting pk_platform_setting; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_setting
    ADD CONSTRAINT pk_platform_setting PRIMARY KEY (id);


--
-- Name: product pk_product; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product
    ADD CONSTRAINT pk_product PRIMARY KEY (id);


--
-- Name: property pk_property; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.property
    ADD CONSTRAINT pk_property PRIMARY KEY (id);


--
-- Name: qa_cert pk_qa_cert; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert
    ADD CONSTRAINT pk_qa_cert PRIMARY KEY (id);


--
-- Name: qa_cert_variant pk_qa_cert_variant; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert_variant
    ADD CONSTRAINT pk_qa_cert_variant PRIMARY KEY (id);


--
-- Name: qa_eval_fail pk_qa_eval_fail; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_fail
    ADD CONSTRAINT pk_qa_eval_fail PRIMARY KEY (id);


--
-- Name: qa_eval_question pk_qa_eval_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_question
    ADD CONSTRAINT pk_qa_eval_question PRIMARY KEY (id);


--
-- Name: qa_eval_run pk_qa_eval_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_run
    ADD CONSTRAINT pk_qa_eval_run PRIMARY KEY (id);


--
-- Name: qa_favorite pk_qa_favorite; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_favorite
    ADD CONSTRAINT pk_qa_favorite PRIMARY KEY (id);


--
-- Name: qa_feedback pk_qa_feedback; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT pk_qa_feedback PRIMARY KEY (id);


--
-- Name: qa_freq_question pk_qa_freq_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_freq_question
    ADD CONSTRAINT pk_qa_freq_question PRIMARY KEY (id);


--
-- Name: qa_object_type_view pk_qa_object_type_view; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_object_type_view
    ADD CONSTRAINT pk_qa_object_type_view PRIMARY KEY (id);


--
-- Name: qa_page pk_qa_page; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page
    ADD CONSTRAINT pk_qa_page PRIMARY KEY (id);


--
-- Name: qa_page_card pk_qa_page_card; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_card
    ADD CONSTRAINT pk_qa_page_card PRIMARY KEY (id);


--
-- Name: qa_page_share_member pk_qa_page_share_member; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_share_member
    ADD CONSTRAINT pk_qa_page_share_member PRIMARY KEY (id);


--
-- Name: qa_page_subscription pk_qa_page_subscription; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_subscription
    ADD CONSTRAINT pk_qa_page_subscription PRIMARY KEY (id);


--
-- Name: qa_permission_request pk_qa_permission_request; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_permission_request
    ADD CONSTRAINT pk_qa_permission_request PRIMARY KEY (id);


--
-- Name: qa_query_log pk_qa_query_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_query_log
    ADD CONSTRAINT pk_qa_query_log PRIMARY KEY (id);


--
-- Name: qa_session pk_qa_session; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session
    ADD CONSTRAINT pk_qa_session PRIMARY KEY (id);


--
-- Name: qa_session_option pk_qa_session_option; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session_option
    ADD CONSTRAINT pk_qa_session_option PRIMARY KEY (id);


--
-- Name: qa_synonym_link pk_qa_synonym_link; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_synonym_link
    ADD CONSTRAINT pk_qa_synonym_link PRIMARY KEY (id);


--
-- Name: qa_template pk_qa_template; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template
    ADD CONSTRAINT pk_qa_template PRIMARY KEY (id);


--
-- Name: qa_template_card pk_qa_template_card; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template_card
    ADD CONSTRAINT pk_qa_template_card PRIMARY KEY (id);


--
-- Name: qa_turn pk_qa_turn; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn
    ADD CONSTRAINT pk_qa_turn PRIMARY KEY (id);


--
-- Name: qa_turn_event pk_qa_turn_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn_event
    ADD CONSTRAINT pk_qa_turn_event PRIMARY KEY (id);


--
-- Name: quality_check pk_quality_check; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_check
    ADD CONSTRAINT pk_quality_check PRIMARY KEY (id);


--
-- Name: quality_run pk_quality_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_run
    ADD CONSTRAINT pk_quality_run PRIMARY KEY (id);


--
-- Name: quality_schedule_cursor pk_quality_schedule_cursor; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_schedule_cursor
    ADD CONSTRAINT pk_quality_schedule_cursor PRIMARY KEY (id);


--
-- Name: reconcile_task pk_reconcile_task; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconcile_task
    ADD CONSTRAINT pk_reconcile_task PRIMARY KEY (id);


--
-- Name: rule pk_rule; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT pk_rule PRIMARY KEY (id);


--
-- Name: rule_draft pk_rule_draft; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_draft
    ADD CONSTRAINT pk_rule_draft PRIMARY KEY (id);


--
-- Name: rule_hit pk_rule_hit; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_hit
    ADD CONSTRAINT pk_rule_hit PRIMARY KEY (id);


--
-- Name: rule_schedule_cursor pk_rule_schedule_cursor; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_cursor
    ADD CONSTRAINT pk_rule_schedule_cursor PRIMARY KEY (id);


--
-- Name: rule_schedule_run pk_rule_schedule_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_run
    ADD CONSTRAINT pk_rule_schedule_run PRIMARY KEY (id);


--
-- Name: rule_version pk_rule_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_version
    ADD CONSTRAINT pk_rule_version PRIMARY KEY (id);


--
-- Name: scenario pk_scenario; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario
    ADD CONSTRAINT pk_scenario PRIMARY KEY (id);


--
-- Name: scenario_impact pk_scenario_impact; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_impact
    ADD CONSTRAINT pk_scenario_impact PRIMARY KEY (id);


--
-- Name: scenario_result pk_scenario_result; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_result
    ADD CONSTRAINT pk_scenario_result PRIMARY KEY (id);


--
-- Name: scenario_template pk_scenario_template; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_template
    ADD CONSTRAINT pk_scenario_template PRIMARY KEY (id);


--
-- Name: service_account pk_service_account; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_account
    ADD CONSTRAINT pk_service_account PRIMARY KEY (id);


--
-- Name: side_effect pk_side_effect; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.side_effect
    ADD CONSTRAINT pk_side_effect PRIMARY KEY (id);


--
-- Name: store pk_store; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store
    ADD CONSTRAINT pk_store PRIMARY KEY (id);


--
-- Name: submission_criterion pk_submission_criterion; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_criterion
    ADD CONSTRAINT pk_submission_criterion PRIMARY KEY (id);


--
-- Name: sys_org pk_sys_org; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_org
    ADD CONSTRAINT pk_sys_org PRIMARY KEY (id);


--
-- Name: sys_role pk_sys_role; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role
    ADD CONSTRAINT pk_sys_role PRIMARY KEY (id);


--
-- Name: sys_role_member pk_sys_role_member; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_member
    ADD CONSTRAINT pk_sys_role_member PRIMARY KEY (id);


--
-- Name: sys_role_scope pk_sys_role_scope; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_scope
    ADD CONSTRAINT pk_sys_role_scope PRIMARY KEY (id);


--
-- Name: sys_tenant pk_sys_tenant; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_tenant
    ADD CONSTRAINT pk_sys_tenant PRIMARY KEY (id);


--
-- Name: sys_user pk_sys_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_user
    ADD CONSTRAINT pk_sys_user PRIMARY KEY (id);


--
-- Name: writeback_call pk_writeback_call; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.writeback_call
    ADD CONSTRAINT pk_writeback_call PRIMARY KEY (id);


--
-- Name: procrastinate_events procrastinate_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_events
    ADD CONSTRAINT procrastinate_events_pkey PRIMARY KEY (id);


--
-- Name: procrastinate_jobs procrastinate_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_jobs
    ADD CONSTRAINT procrastinate_jobs_pkey PRIMARY KEY (id);


--
-- Name: procrastinate_periodic_defers procrastinate_periodic_defers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers
    ADD CONSTRAINT procrastinate_periodic_defers_pkey PRIMARY KEY (id);


--
-- Name: procrastinate_periodic_defers procrastinate_periodic_defers_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers
    ADD CONSTRAINT procrastinate_periodic_defers_unique UNIQUE (task_name, periodic_id, defer_timestamp);


--
-- Name: procrastinate_workers procrastinate_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_workers
    ADD CONSTRAINT procrastinate_workers_pkey PRIMARY KEY (id);


--
-- Name: rescue_incident_w4_demo rescue_incident_w4_demo_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rescue_incident_w4_demo
    ADD CONSTRAINT rescue_incident_w4_demo_code_key UNIQUE (code);


--
-- Name: rescue_incident_w4_demo rescue_incident_w4_demo_incident_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rescue_incident_w4_demo
    ADD CONSTRAINT rescue_incident_w4_demo_incident_id_key UNIQUE (incident_id);


--
-- Name: rescue_incident_w4_demo rescue_incident_w4_demo_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rescue_incident_w4_demo
    ADD CONSTRAINT rescue_incident_w4_demo_pkey PRIMARY KEY (id);


--
-- Name: rescue_team_w4_demo rescue_team_w4_demo_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rescue_team_w4_demo
    ADD CONSTRAINT rescue_team_w4_demo_code_key UNIQUE (code);


--
-- Name: rescue_team_w4_demo rescue_team_w4_demo_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rescue_team_w4_demo
    ADD CONSTRAINT rescue_team_w4_demo_pkey PRIMARY KEY (id);


--
-- Name: rescue_team_w4_demo rescue_team_w4_demo_team_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rescue_team_w4_demo
    ADD CONSTRAINT rescue_team_w4_demo_team_id_key UNIQUE (team_id);


--
-- Name: action_param uq_action_param_ontology_id_action_type_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_param
    ADD CONSTRAINT uq_action_param_ontology_id_action_type_id_seq UNIQUE (ontology_id, action_type_id, seq);


--
-- Name: action_run_daily_counter uq_action_run_daily_counter_ontology_id_day; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_counter
    ADD CONSTRAINT uq_action_run_daily_counter_ontology_id_day UNIQUE (ontology_id, day);


--
-- Name: action_run_daily_stat uq_action_run_daily_stat_ontology_id_action_type_id_stat_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_stat
    ADD CONSTRAINT uq_action_run_daily_stat_ontology_id_action_type_id_stat_date UNIQUE (ontology_id, action_type_id, stat_date);


--
-- Name: action_run_log uq_action_run_log_ontology_id_action_run_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_log
    ADD CONSTRAINT uq_action_run_log_ontology_id_action_run_id_seq UNIQUE (ontology_id, action_run_id, seq);


--
-- Name: action_run uq_action_run_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT uq_action_run_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: action_type uq_action_type_ontology_id_api; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type
    ADD CONSTRAINT uq_action_type_ontology_id_api UNIQUE (ontology_id, api);


--
-- Name: action_type uq_action_type_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type
    ADD CONSTRAINT uq_action_type_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: action_type_rule uq_action_type_rule_ontology_id_action_type_id_rule_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_rule
    ADD CONSTRAINT uq_action_type_rule_ontology_id_action_type_id_rule_code UNIQUE (ontology_id, action_type_id, rule_code);


--
-- Name: action_type_version uq_action_type_version_ontology_id_action_type_id_ver; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_version
    ADD CONSTRAINT uq_action_type_version_ontology_id_action_type_id_ver UNIQUE (ontology_id, action_type_id, ver);


--
-- Name: alert_log uq_alert_log_ontology_id_alert_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_log
    ADD CONSTRAINT uq_alert_log_ontology_id_alert_id_seq UNIQUE (ontology_id, alert_id, seq);


--
-- Name: alert uq_alert_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert
    ADD CONSTRAINT uq_alert_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: api_key uq_api_key_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT uq_api_key_code UNIQUE (code);


--
-- Name: api_key uq_api_key_token_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT uq_api_key_token_hash UNIQUE (token_hash);


--
-- Name: api_key_usage uq_api_key_usage_key_id_period_bucket; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_usage
    ADD CONSTRAINT uq_api_key_usage_key_id_period_bucket UNIQUE (key_id, period, bucket);


--
-- Name: audit_export_job uq_audit_export_job_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_job
    ADD CONSTRAINT uq_audit_export_job_code UNIQUE (code);


--
-- Name: audit_export_row uq_audit_export_row_job_id_audit_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_row
    ADD CONSTRAINT uq_audit_export_row_job_id_audit_id UNIQUE (job_id, audit_id);


--
-- Name: audit_log uq_audit_log_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT uq_audit_log_code UNIQUE (code);


--
-- Name: biz_order uq_biz_order_ontology_id_code_created_at_biz; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_order
    ADD CONSTRAINT uq_biz_order_ontology_id_code_created_at_biz UNIQUE (ontology_id, code, created_at_biz);


--
-- Name: change_request uq_change_request_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_request
    ADD CONSTRAINT uq_change_request_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: cr_check uq_cr_check_ontology_id_cr_id_code_target_db; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_check
    ADD CONSTRAINT uq_cr_check_ontology_id_cr_id_code_target_db UNIQUE (ontology_id, cr_id, code, target_db);


--
-- Name: cr_item uq_cr_item_ontology_id_cr_id_target_kind_target_id_sect_0e9d; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_item
    ADD CONSTRAINT uq_cr_item_ontology_id_cr_id_target_kind_target_id_sect_0e9d UNIQUE (ontology_id, cr_id, target_kind, target_id, section, item_key);


--
-- Name: cr_target uq_cr_target_ontology_id_cr_id_target_kind_target_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_target
    ADD CONSTRAINT uq_cr_target_ontology_id_cr_id_target_kind_target_id UNIQUE (ontology_id, cr_id, target_kind, target_id);


--
-- Name: cr_trial uq_cr_trial_ontology_id_cr_id_period; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_trial
    ADD CONSTRAINT uq_cr_trial_ontology_id_cr_id_period UNIQUE (ontology_id, cr_id, period);


--
-- Name: datasource uq_datasource_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource
    ADD CONSTRAINT uq_datasource_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: datasource_run uq_datasource_run_ontology_id_datasource_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_run
    ADD CONSTRAINT uq_datasource_run_ontology_id_datasource_id_seq UNIQUE (ontology_id, datasource_id, seq);


--
-- Name: datasource_table uq_datasource_table_ontology_id_datasource_id_schema_name_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_table
    ADD CONSTRAINT uq_datasource_table_ontology_id_datasource_id_schema_name_name UNIQUE (ontology_id, datasource_id, schema_name, name);


--
-- Name: decision_log uq_decision_log_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_log
    ADD CONSTRAINT uq_decision_log_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: dim_level uq_dim_level_ontology_id_dimension_id_level_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_level
    ADD CONSTRAINT uq_dim_level_ontology_id_dimension_id_level_no UNIQUE (ontology_id, dimension_id, level_no);


--
-- Name: dim_mapping uq_dim_mapping_ontology_id_dimension_id_map_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_mapping
    ADD CONSTRAINT uq_dim_mapping_ontology_id_dimension_id_map_key UNIQUE (ontology_id, dimension_id, map_key);


--
-- Name: dim_value uq_dim_value_ontology_id_dimension_id_value_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_value
    ADD CONSTRAINT uq_dim_value_ontology_id_dimension_id_value_key UNIQUE (ontology_id, dimension_id, value_key);


--
-- Name: dimension uq_dimension_ontology_id_api_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dimension
    ADD CONSTRAINT uq_dimension_ontology_id_api_name UNIQUE (ontology_id, api_name);


--
-- Name: dimension uq_dimension_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dimension
    ADD CONSTRAINT uq_dimension_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: event_daily_stat uq_event_daily_stat_ontology_id_event_type_id_stat_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_daily_stat
    ADD CONSTRAINT uq_event_daily_stat_ontology_id_event_type_id_stat_date UNIQUE (ontology_id, event_type_id, stat_date);


--
-- Name: event_dedup uq_event_dedup_ontology_id_event_type_id_dedup_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_dedup
    ADD CONSTRAINT uq_event_dedup_ontology_id_event_type_id_dedup_key UNIQUE (ontology_id, event_type_id, dedup_key);


--
-- Name: event_delivery uq_event_delivery_ontology_id_event_id_subscriber_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery
    ADD CONSTRAINT uq_event_delivery_ontology_id_event_id_subscriber_name UNIQUE (ontology_id, event_id, subscriber_name);


--
-- Name: event_envelope_field uq_event_envelope_field_ontology_id_api; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_envelope_field
    ADD CONSTRAINT uq_event_envelope_field_ontology_id_api UNIQUE (ontology_id, api);


--
-- Name: event uq_event_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT uq_event_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: event_payload_field uq_event_payload_field_ontology_id_event_type_id_api; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_payload_field
    ADD CONSTRAINT uq_event_payload_field_ontology_id_event_type_id_api UNIQUE (ontology_id, event_type_id, api);


--
-- Name: event_subscription uq_event_subscription_ontology_id_event_type_id_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_subscription
    ADD CONSTRAINT uq_event_subscription_ontology_id_event_type_id_name UNIQUE (ontology_id, event_type_id, name);


--
-- Name: event_type uq_event_type_ontology_id_api; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type
    ADD CONSTRAINT uq_event_type_ontology_id_api UNIQUE (ontology_id, api);


--
-- Name: event_type uq_event_type_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type
    ADD CONSTRAINT uq_event_type_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: event_type_version uq_event_type_version_ontology_id_event_type_id_ver; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type_version
    ADD CONSTRAINT uq_event_type_version_ontology_id_event_type_id_ver UNIQUE (ontology_id, event_type_id, ver);


--
-- Name: ext_system uq_ext_system_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_system
    ADD CONSTRAINT uq_ext_system_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: ext_system uq_ext_system_ontology_id_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_system
    ADD CONSTRAINT uq_ext_system_ontology_id_name UNIQUE (ontology_id, name);


--
-- Name: fn_param uq_fn_param_ontology_id_function_id_direction_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_param
    ADD CONSTRAINT uq_fn_param_ontology_id_function_id_direction_seq UNIQUE (ontology_id, function_id, direction, seq);


--
-- Name: fn_version uq_fn_version_ontology_id_function_id_ver; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_version
    ADD CONSTRAINT uq_fn_version_ontology_id_function_id_ver UNIQUE (ontology_id, function_id, ver);


--
-- Name: function uq_function_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.function
    ADD CONSTRAINT uq_function_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: function uq_function_ontology_id_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.function
    ADD CONSTRAINT uq_function_ontology_id_name UNIQUE (ontology_id, name);


--
-- Name: governed_binding uq_governed_binding_ontology_id_object_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governed_binding
    ADD CONSTRAINT uq_governed_binding_ontology_id_object_code UNIQUE (ontology_id, object_code);


--
-- Name: governed_binding uq_governed_binding_ontology_id_table_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governed_binding
    ADD CONSTRAINT uq_governed_binding_ontology_id_table_name UNIQUE (ontology_id, table_name);


--
-- Name: knowledge_apply uq_knowledge_apply_ontology_id_knowledge_id_target_kind_0377; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_apply
    ADD CONSTRAINT uq_knowledge_apply_ontology_id_knowledge_id_target_kind_0377 UNIQUE (ontology_id, knowledge_id, target_kind, target_code);


--
-- Name: knowledge uq_knowledge_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge
    ADD CONSTRAINT uq_knowledge_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: lifecycle_change uq_lifecycle_change_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_change
    ADD CONSTRAINT uq_lifecycle_change_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: lifecycle_draft uq_lifecycle_draft_ontology_id_lifecycle_id_cr_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_draft
    ADD CONSTRAINT uq_lifecycle_draft_ontology_id_lifecycle_id_cr_code UNIQUE (ontology_id, lifecycle_id, cr_code);


--
-- Name: lifecycle uq_lifecycle_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle
    ADD CONSTRAINT uq_lifecycle_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: lifecycle uq_lifecycle_ontology_id_object_type_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle
    ADD CONSTRAINT uq_lifecycle_ontology_id_object_type_id UNIQUE (ontology_id, object_type_id);


--
-- Name: lifecycle_state uq_lifecycle_state_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_state
    ADD CONSTRAINT uq_lifecycle_state_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: lifecycle_state uq_lifecycle_state_ontology_id_version_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_state
    ADD CONSTRAINT uq_lifecycle_state_ontology_id_version_id_key UNIQUE (ontology_id, version_id, key);


--
-- Name: lifecycle_transition uq_lifecycle_transition_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT uq_lifecycle_transition_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: lifecycle_transition uq_lifecycle_transition_ontology_id_version_id_tr_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT uq_lifecycle_transition_ontology_id_version_id_tr_code UNIQUE (ontology_id, version_id, tr_code);


--
-- Name: lifecycle_version uq_lifecycle_version_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_version
    ADD CONSTRAINT uq_lifecycle_version_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: lifecycle_version uq_lifecycle_version_ontology_id_lifecycle_id_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_version
    ADD CONSTRAINT uq_lifecycle_version_ontology_id_lifecycle_id_version UNIQUE (ontology_id, lifecycle_id, version);


--
-- Name: link_quality uq_link_quality_ontology_id_link_type_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_quality
    ADD CONSTRAINT uq_link_quality_ontology_id_link_type_id UNIQUE (ontology_id, link_type_id);


--
-- Name: link_type uq_link_type_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_type
    ADD CONSTRAINT uq_link_type_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: metric uq_metric_ontology_id_api_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric
    ADD CONSTRAINT uq_metric_ontology_id_api_name UNIQUE (ontology_id, api_name);


--
-- Name: metric uq_metric_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric
    ADD CONSTRAINT uq_metric_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: notification uq_notification_ontology_id_source_event_id_recipient_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT uq_notification_ontology_id_source_event_id_recipient_id UNIQUE (ontology_id, source_event_id, recipient_id);


--
-- Name: notification uq_notification_outbox_recipient; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT uq_notification_outbox_recipient UNIQUE (ontology_id, source_outbox_id, recipient_id);


--
-- Name: object_type uq_object_type_ontology_id_api_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_type
    ADD CONSTRAINT uq_object_type_ontology_id_api_name UNIQUE (ontology_id, api_name);


--
-- Name: object_type uq_object_type_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_type
    ADD CONSTRAINT uq_object_type_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: object_view uq_object_view_ontology_id_object_type_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_view
    ADD CONSTRAINT uq_object_view_ontology_id_object_type_id_code UNIQUE (ontology_id, object_type_id, code);


--
-- Name: onto_qa_path_example uq_onto_qa_path_example_ontology_id_from_object_id_to_o_723b; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_example
    ADD CONSTRAINT uq_onto_qa_path_example_ontology_id_from_object_id_to_o_723b UNIQUE (ontology_id, from_object_id, to_object_id, seq);


--
-- Name: onto_qa_path uq_onto_qa_path_ontology_id_from_object_id_to_object_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path
    ADD CONSTRAINT uq_onto_qa_path_ontology_id_from_object_id_to_object_id_seq UNIQUE (ontology_id, from_object_id, to_object_id, seq);


--
-- Name: onto_qa_path_step uq_onto_qa_path_step_ontology_id_path_id_step_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_step
    ADD CONSTRAINT uq_onto_qa_path_step_ontology_id_path_id_step_no UNIQUE (ontology_id, path_id, step_no);


--
-- Name: ontology_edit_rule uq_ontology_edit_rule_ontology_id_action_type_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_edit_rule
    ADD CONSTRAINT uq_ontology_edit_rule_ontology_id_action_type_id_seq UNIQUE (ontology_id, action_type_id, seq);


--
-- Name: ontology_member uq_ontology_member_ontology_id_user_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_member
    ADD CONSTRAINT uq_ontology_member_ontology_id_user_id UNIQUE (ontology_id, user_id);


--
-- Name: ontology_release uq_ontology_release_ontology_id_ver; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_release
    ADD CONSTRAINT uq_ontology_release_ontology_id_ver UNIQUE (ontology_id, ver);


--
-- Name: ontology_role_binding uq_ontology_role_binding_ontology_id_role_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_role_binding
    ADD CONSTRAINT uq_ontology_role_binding_ontology_id_role_id UNIQUE (ontology_id, role_id);


--
-- Name: ontology_workspace uq_ontology_workspace_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_workspace
    ADD CONSTRAINT uq_ontology_workspace_code UNIQUE (code);


--
-- Name: order_line uq_order_line_ontology_id_order_id_line_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_line
    ADD CONSTRAINT uq_order_line_ontology_id_order_id_line_no UNIQUE (ontology_id, order_id, line_no);


--
-- Name: outbox_event uq_outbox_event_ontology_id_event_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_event
    ADD CONSTRAINT uq_outbox_event_ontology_id_event_id UNIQUE (ontology_id, event_id);


--
-- Name: platform_setting uq_platform_setting_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_setting
    ADD CONSTRAINT uq_platform_setting_code UNIQUE (code);


--
-- Name: product uq_product_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product
    ADD CONSTRAINT uq_product_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: property uq_property_ontology_id_object_type_id_api_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.property
    ADD CONSTRAINT uq_property_ontology_id_object_type_id_api_name UNIQUE (ontology_id, object_type_id, api_name);


--
-- Name: qa_cert uq_qa_cert_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert
    ADD CONSTRAINT uq_qa_cert_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_cert_variant uq_qa_cert_variant_ontology_id_cert_id_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert_variant
    ADD CONSTRAINT uq_qa_cert_variant_ontology_id_cert_id_question UNIQUE (ontology_id, cert_id, question);


--
-- Name: qa_eval_fail uq_qa_eval_fail_ontology_id_run_id_question_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_fail
    ADD CONSTRAINT uq_qa_eval_fail_ontology_id_run_id_question_id UNIQUE (ontology_id, run_id, question_id);


--
-- Name: qa_eval_question uq_qa_eval_question_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_question
    ADD CONSTRAINT uq_qa_eval_question_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_eval_question uq_qa_eval_question_ontology_id_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_question
    ADD CONSTRAINT uq_qa_eval_question_ontology_id_question UNIQUE (ontology_id, question);


--
-- Name: qa_eval_run uq_qa_eval_run_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_run
    ADD CONSTRAINT uq_qa_eval_run_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_favorite uq_qa_favorite_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_favorite
    ADD CONSTRAINT uq_qa_favorite_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_favorite uq_qa_favorite_ontology_id_owner_id_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_favorite
    ADD CONSTRAINT uq_qa_favorite_ontology_id_owner_id_question UNIQUE (ontology_id, owner_id, question);


--
-- Name: qa_feedback uq_qa_feedback_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT uq_qa_feedback_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_freq_question uq_qa_freq_question_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_freq_question
    ADD CONSTRAINT uq_qa_freq_question_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_freq_question uq_qa_freq_question_ontology_id_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_freq_question
    ADD CONSTRAINT uq_qa_freq_question_ontology_id_question UNIQUE (ontology_id, question);


--
-- Name: qa_object_type_view uq_qa_object_type_view_ontology_id_kind_release_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_object_type_view
    ADD CONSTRAINT uq_qa_object_type_view_ontology_id_kind_release_id UNIQUE NULLS NOT DISTINCT (ontology_id, kind, release_id);


--
-- Name: qa_page_card uq_qa_page_card_ontology_id_page_id_card_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_card
    ADD CONSTRAINT uq_qa_page_card_ontology_id_page_id_card_key UNIQUE (ontology_id, page_id, card_key);


--
-- Name: qa_page uq_qa_page_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page
    ADD CONSTRAINT uq_qa_page_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_page uq_qa_page_ontology_id_owner_id_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page
    ADD CONSTRAINT uq_qa_page_ontology_id_owner_id_name UNIQUE (ontology_id, owner_id, name);


--
-- Name: qa_page_share_member uq_qa_page_share_member_ontology_id_page_id_user_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_share_member
    ADD CONSTRAINT uq_qa_page_share_member_ontology_id_page_id_user_id UNIQUE (ontology_id, page_id, user_id);


--
-- Name: qa_page_subscription uq_qa_page_subscription_ontology_id_page_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_subscription
    ADD CONSTRAINT uq_qa_page_subscription_ontology_id_page_id UNIQUE (ontology_id, page_id);


--
-- Name: qa_permission_request uq_qa_permission_request_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_permission_request
    ADD CONSTRAINT uq_qa_permission_request_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_session uq_qa_session_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session
    ADD CONSTRAINT uq_qa_session_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_session_option uq_qa_session_option_ontology_id_kind_option_value; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session_option
    ADD CONSTRAINT uq_qa_session_option_ontology_id_kind_option_value UNIQUE (ontology_id, kind, option_value);


--
-- Name: qa_synonym_link uq_qa_synonym_link_ontology_id_word_cr_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_synonym_link
    ADD CONSTRAINT uq_qa_synonym_link_ontology_id_word_cr_code UNIQUE NULLS NOT DISTINCT (ontology_id, word, cr_code);


--
-- Name: qa_template_card uq_qa_template_card_ontology_id_template_id_card_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template_card
    ADD CONSTRAINT uq_qa_template_card_ontology_id_template_id_card_key UNIQUE (ontology_id, template_id, card_key);


--
-- Name: qa_template uq_qa_template_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template
    ADD CONSTRAINT uq_qa_template_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_template uq_qa_template_ontology_id_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template
    ADD CONSTRAINT uq_qa_template_ontology_id_name UNIQUE (ontology_id, name);


--
-- Name: qa_turn_event uq_qa_turn_event_ontology_id_turn_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn_event
    ADD CONSTRAINT uq_qa_turn_event_ontology_id_turn_id_seq UNIQUE (ontology_id, turn_id, seq);


--
-- Name: qa_turn uq_qa_turn_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn
    ADD CONSTRAINT uq_qa_turn_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: qa_turn uq_qa_turn_ontology_id_session_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn
    ADD CONSTRAINT uq_qa_turn_ontology_id_session_id_seq UNIQUE (ontology_id, session_id, seq);


--
-- Name: quality_check uq_quality_check_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_check
    ADD CONSTRAINT uq_quality_check_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: quality_schedule_cursor uq_quality_schedule_cursor_ontology_id_check_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_schedule_cursor
    ADD CONSTRAINT uq_quality_schedule_cursor_ontology_id_check_id UNIQUE (ontology_id, check_id);


--
-- Name: reconcile_task uq_reconcile_task_ontology_id_action_run_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconcile_task
    ADD CONSTRAINT uq_reconcile_task_ontology_id_action_run_id UNIQUE (ontology_id, action_run_id);


--
-- Name: rule_draft uq_rule_draft_ontology_id_rule_id_cr_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_draft
    ADD CONSTRAINT uq_rule_draft_ontology_id_rule_id_cr_code UNIQUE (ontology_id, rule_id, cr_code);


--
-- Name: rule_hit uq_rule_hit_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_hit
    ADD CONSTRAINT uq_rule_hit_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: rule uq_rule_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT uq_rule_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: rule_schedule_cursor uq_rule_schedule_cursor_ontology_id_rule_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_cursor
    ADD CONSTRAINT uq_rule_schedule_cursor_ontology_id_rule_id UNIQUE (ontology_id, rule_id);


--
-- Name: rule_schedule_run uq_rule_schedule_run_ontology_id_rule_id_rule_version_s_aa9f; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_run
    ADD CONSTRAINT uq_rule_schedule_run_ontology_id_rule_id_rule_version_s_aa9f UNIQUE (ontology_id, rule_id, rule_version, scheduled_at);


--
-- Name: rule_version uq_rule_version_ontology_id_rule_id_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_version
    ADD CONSTRAINT uq_rule_version_ontology_id_rule_id_version UNIQUE (ontology_id, rule_id, version);


--
-- Name: scenario_impact uq_scenario_impact_ontology_id_scenario_id_sku_store_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_impact
    ADD CONSTRAINT uq_scenario_impact_ontology_id_scenario_id_sku_store_id UNIQUE (ontology_id, scenario_id, sku, store_id);


--
-- Name: scenario uq_scenario_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario
    ADD CONSTRAINT uq_scenario_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: scenario_result uq_scenario_result_ontology_id_scenario_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_result
    ADD CONSTRAINT uq_scenario_result_ontology_id_scenario_id UNIQUE (ontology_id, scenario_id);


--
-- Name: scenario_template uq_scenario_template_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_template
    ADD CONSTRAINT uq_scenario_template_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: service_account uq_service_account_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_account
    ADD CONSTRAINT uq_service_account_code UNIQUE (code);


--
-- Name: service_account uq_service_account_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_account
    ADD CONSTRAINT uq_service_account_name UNIQUE (name);


--
-- Name: side_effect uq_side_effect_ontology_id_action_type_id_target; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.side_effect
    ADD CONSTRAINT uq_side_effect_ontology_id_action_type_id_target UNIQUE (ontology_id, action_type_id, target);


--
-- Name: store uq_store_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store
    ADD CONSTRAINT uq_store_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: store uq_store_ontology_id_store_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store
    ADD CONSTRAINT uq_store_ontology_id_store_no UNIQUE (ontology_id, store_no);


--
-- Name: submission_criterion uq_submission_criterion_ontology_id_action_type_id_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_criterion
    ADD CONSTRAINT uq_submission_criterion_ontology_id_action_type_id_name UNIQUE (ontology_id, action_type_id, name);


--
-- Name: sys_org uq_sys_org_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_org
    ADD CONSTRAINT uq_sys_org_code UNIQUE (code);


--
-- Name: sys_role uq_sys_role_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role
    ADD CONSTRAINT uq_sys_role_code UNIQUE (code);


--
-- Name: sys_role_member uq_sys_role_member_role_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_member
    ADD CONSTRAINT uq_sys_role_member_role_user UNIQUE (role_id, user_id);


--
-- Name: sys_role uq_sys_role_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role
    ADD CONSTRAINT uq_sys_role_name UNIQUE (name);


--
-- Name: sys_role_scope uq_sys_role_scope_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_scope
    ADD CONSTRAINT uq_sys_role_scope_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: sys_tenant uq_sys_tenant_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_tenant
    ADD CONSTRAINT uq_sys_tenant_code UNIQUE (code);


--
-- Name: sys_user uq_sys_user_account; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_user
    ADD CONSTRAINT uq_sys_user_account UNIQUE (account);


--
-- Name: sys_user uq_sys_user_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_user
    ADD CONSTRAINT uq_sys_user_code UNIQUE (code);


--
-- Name: writeback_call uq_writeback_call_ontology_id_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.writeback_call
    ADD CONSTRAINT uq_writeback_call_ontology_id_code UNIQUE (ontology_id, code);


--
-- Name: idx_procrastinate_jobs_worker_not_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_procrastinate_jobs_worker_not_null ON public.procrastinate_jobs USING btree (worker_id) WHERE ((worker_id IS NOT NULL) AND (status = 'doing'::public.procrastinate_job_status));


--
-- Name: idx_procrastinate_workers_last_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_procrastinate_workers_last_heartbeat ON public.procrastinate_workers USING btree (last_heartbeat);


--
-- Name: ix_action_param_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_param_ontology_id ON public.action_param USING btree (ontology_id);


--
-- Name: ix_action_run_action_type_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_action_type_code ON public.action_run USING btree (action_type_code);


--
-- Name: ix_action_run_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_alert_id ON public.action_run USING btree (alert_id);


--
-- Name: ix_action_run_center_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_center_status ON public.action_run USING btree (in_center, status);


--
-- Name: ix_action_run_daily_counter_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_daily_counter_ontology_id ON public.action_run_daily_counter USING btree (ontology_id);


--
-- Name: ix_action_run_daily_stat_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_daily_stat_ontology_id ON public.action_run_daily_stat USING btree (ontology_id);


--
-- Name: ix_action_run_idem_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_idem_key ON public.action_run USING btree (idem_key);


--
-- Name: ix_action_run_log_action_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_log_action_run_id ON public.action_run_log USING btree (action_run_id);


--
-- Name: ix_action_run_log_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_log_ontology_id ON public.action_run_log USING btree (ontology_id);


--
-- Name: ix_action_run_obj_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_obj_id ON public.action_run USING btree (obj_id);


--
-- Name: ix_action_run_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_ontology_id ON public.action_run USING btree (ontology_id);


--
-- Name: ix_action_run_rule_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_rule_code ON public.action_run USING btree (rule_code);


--
-- Name: ix_action_run_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_status ON public.action_run USING btree (status);


--
-- Name: ix_action_run_submitted_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_run_submitted_at ON public.action_run USING btree (submitted_at);


--
-- Name: ix_action_type_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_type_ontology_id ON public.action_type USING btree (ontology_id);


--
-- Name: ix_action_type_rule_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_type_rule_ontology_id ON public.action_type_rule USING btree (ontology_id);


--
-- Name: ix_action_type_sys_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_type_sys_id ON public.action_type USING btree (sys_id);


--
-- Name: ix_action_type_version_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_type_version_ontology_id ON public.action_type_version USING btree (ontology_id);


--
-- Name: ix_alert_log_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_log_alert_id ON public.alert_log USING btree (alert_id);


--
-- Name: ix_alert_log_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_log_ontology_id ON public.alert_log USING btree (ontology_id);


--
-- Name: ix_alert_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_ontology_id ON public.alert USING btree (ontology_id);


--
-- Name: ix_alert_rule_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_rule_code ON public.alert USING btree (rule_code);


--
-- Name: ix_alert_rule_obj; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_rule_obj ON public.alert USING btree (rule_code, obj_id);


--
-- Name: ix_alert_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_status ON public.alert USING btree (status);


--
-- Name: ix_alert_triggered_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_triggered_at ON public.alert USING btree (triggered_at);


--
-- Name: ix_api_key_call_key_id_created_at_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_api_key_call_key_id_created_at_id ON public.api_key_call USING btree (key_id, created_at, id);


--
-- Name: ix_api_key_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_api_key_expires_at ON public.api_key USING btree (expires_at);


--
-- Name: ix_audit_log_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_action ON public.audit_log USING btree (action);


--
-- Name: ix_audit_log_time_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_time_id ON public.audit_log USING btree ("time", id);


--
-- Name: ix_biz_order_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_biz_order_channel ON public.biz_order USING btree (channel);


--
-- Name: ix_biz_order_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_biz_order_code ON public.biz_order USING btree (code);


--
-- Name: ix_biz_order_created_at_biz; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_biz_order_created_at_biz ON public.biz_order USING btree (created_at_biz);


--
-- Name: ix_biz_order_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_biz_order_ontology_id ON public.biz_order USING btree (ontology_id);


--
-- Name: ix_biz_order_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_biz_order_status ON public.biz_order USING btree (status);


--
-- Name: ix_biz_order_store_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_biz_order_store_id ON public.biz_order USING btree (store_id);


--
-- Name: ix_change_request_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_request_ontology_id ON public.change_request USING btree (ontology_id);


--
-- Name: ix_change_request_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_request_release_id ON public.change_request USING btree (release_id);


--
-- Name: ix_cr_check_cr_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_check_cr_id ON public.cr_check USING btree (cr_id);


--
-- Name: ix_cr_check_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_check_ontology_id ON public.cr_check USING btree (ontology_id);


--
-- Name: ix_cr_event_cr_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_event_cr_id ON public.cr_event USING btree (cr_id);


--
-- Name: ix_cr_event_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_event_ontology_id ON public.cr_event USING btree (ontology_id);


--
-- Name: ix_cr_impact_cr_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_impact_cr_id ON public.cr_impact USING btree (cr_id);


--
-- Name: ix_cr_impact_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_impact_ontology_id ON public.cr_impact USING btree (ontology_id);


--
-- Name: ix_cr_item_cr_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_item_cr_id ON public.cr_item USING btree (cr_id);


--
-- Name: ix_cr_item_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_item_ontology_id ON public.cr_item USING btree (ontology_id);


--
-- Name: ix_cr_target_cr_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_target_cr_id ON public.cr_target USING btree (cr_id);


--
-- Name: ix_cr_target_kind_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_target_kind_id ON public.cr_target USING btree (target_kind, target_id);


--
-- Name: ix_cr_target_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_target_ontology_id ON public.cr_target USING btree (ontology_id);


--
-- Name: ix_cr_trial_cr_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_trial_cr_id ON public.cr_trial USING btree (cr_id);


--
-- Name: ix_cr_trial_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cr_trial_ontology_id ON public.cr_trial USING btree (ontology_id);


--
-- Name: ix_datasource_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_datasource_ontology_id ON public.datasource USING btree (ontology_id);


--
-- Name: ix_datasource_run_datasource_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_datasource_run_datasource_id ON public.datasource_run USING btree (datasource_id);


--
-- Name: ix_datasource_run_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_datasource_run_ontology_id ON public.datasource_run USING btree (ontology_id);


--
-- Name: ix_datasource_table_datasource_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_datasource_table_datasource_id ON public.datasource_table USING btree (datasource_id);


--
-- Name: ix_datasource_table_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_datasource_table_ontology_id ON public.datasource_table USING btree (ontology_id);


--
-- Name: ix_decision_log_ai_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_decision_log_ai_session_id ON public.decision_log USING btree (ai_session_id);


--
-- Name: ix_decision_log_alert_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_decision_log_alert_code ON public.decision_log USING btree (alert_code);


--
-- Name: ix_decision_log_obj_kind_obj_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_decision_log_obj_kind_obj_id ON public.decision_log USING btree (obj_kind, obj_id);


--
-- Name: ix_decision_log_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_decision_log_ontology_id ON public.decision_log USING btree (ontology_id);


--
-- Name: ix_dim_level_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dim_level_ontology_id ON public.dim_level USING btree (ontology_id);


--
-- Name: ix_dim_mapping_dimension_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dim_mapping_dimension_id ON public.dim_mapping USING btree (dimension_id);


--
-- Name: ix_dim_mapping_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dim_mapping_ontology_id ON public.dim_mapping USING btree (ontology_id);


--
-- Name: ix_dim_value_dimension_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dim_value_dimension_id ON public.dim_value USING btree (dimension_id);


--
-- Name: ix_dim_value_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dim_value_ontology_id ON public.dim_value USING btree (ontology_id);


--
-- Name: ix_dimension_object_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dimension_object_id ON public.dimension USING btree (object_id);


--
-- Name: ix_dimension_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dimension_ontology_id ON public.dimension USING btree (ontology_id);


--
-- Name: ix_event_daily_stat_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_daily_stat_ontology_id ON public.event_daily_stat USING btree (ontology_id);


--
-- Name: ix_event_dedup_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_dedup_ontology_id ON public.event_dedup USING btree (ontology_id);


--
-- Name: ix_event_delivery_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_delivery_ontology_id ON public.event_delivery USING btree (ontology_id);


--
-- Name: ix_event_delivery_record_delivery_id_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_delivery_record_delivery_id_id ON public.event_delivery_record USING btree (delivery_id, id);


--
-- Name: ix_event_delivery_record_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_delivery_record_ontology_id ON public.event_delivery_record USING btree (ontology_id);


--
-- Name: ix_event_delivery_state_next_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_delivery_state_next_at ON public.event_delivery USING btree (state, next_at);


--
-- Name: ix_event_envelope_field_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_envelope_field_ontology_id ON public.event_envelope_field USING btree (ontology_id);


--
-- Name: ix_event_event_type_id_occurred_at_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_event_type_id_occurred_at_id ON public.event USING btree (event_type_id, occurred_at, id);


--
-- Name: ix_event_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_ontology_id ON public.event USING btree (ontology_id);


--
-- Name: ix_event_payload_field_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_payload_field_ontology_id ON public.event_payload_field USING btree (ontology_id);


--
-- Name: ix_event_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_release_id ON public.event USING btree (release_id);


--
-- Name: ix_event_subscription_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_subscription_ontology_id ON public.event_subscription USING btree (ontology_id);


--
-- Name: ix_event_type_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_type_ontology_id ON public.event_type USING btree (ontology_id);


--
-- Name: ix_event_type_version_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_type_version_ontology_id ON public.event_type_version USING btree (ontology_id);


--
-- Name: ix_ext_system_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ext_system_ontology_id ON public.ext_system USING btree (ontology_id);


--
-- Name: ix_fn_param_function_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fn_param_function_id ON public.fn_param USING btree (function_id);


--
-- Name: ix_fn_param_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fn_param_ontology_id ON public.fn_param USING btree (ontology_id);


--
-- Name: ix_fn_use_function_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fn_use_function_id ON public.fn_use USING btree (function_id);


--
-- Name: ix_fn_use_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fn_use_ontology_id ON public.fn_use USING btree (ontology_id);


--
-- Name: ix_fn_version_function_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fn_version_function_id ON public.fn_version USING btree (function_id);


--
-- Name: ix_fn_version_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fn_version_ontology_id ON public.fn_version USING btree (ontology_id);


--
-- Name: ix_function_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_function_ontology_id ON public.function USING btree (ontology_id);


--
-- Name: ix_knowledge_apply_knowledge_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_apply_knowledge_id ON public.knowledge_apply USING btree (knowledge_id);


--
-- Name: ix_knowledge_apply_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_apply_ontology_id ON public.knowledge_apply USING btree (ontology_id);


--
-- Name: ix_knowledge_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_ontology_id ON public.knowledge USING btree (ontology_id);


--
-- Name: ix_lifecycle_change_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_change_at ON public.lifecycle_change USING btree (at);


--
-- Name: ix_lifecycle_change_lifecycle_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_change_lifecycle_id ON public.lifecycle_change USING btree (lifecycle_id);


--
-- Name: ix_lifecycle_change_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_change_ontology_id ON public.lifecycle_change USING btree (ontology_id);


--
-- Name: ix_lifecycle_draft_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_draft_ontology_id ON public.lifecycle_draft USING btree (ontology_id);


--
-- Name: ix_lifecycle_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_ontology_id ON public.lifecycle USING btree (ontology_id);


--
-- Name: ix_lifecycle_state_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_state_ontology_id ON public.lifecycle_state USING btree (ontology_id);


--
-- Name: ix_lifecycle_state_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_state_version_id ON public.lifecycle_state USING btree (version_id);


--
-- Name: ix_lifecycle_transition_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_transition_ontology_id ON public.lifecycle_transition USING btree (ontology_id);


--
-- Name: ix_lifecycle_transition_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_transition_version_id ON public.lifecycle_transition USING btree (version_id);


--
-- Name: ix_lifecycle_version_lifecycle_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_version_lifecycle_id ON public.lifecycle_version USING btree (lifecycle_id);


--
-- Name: ix_lifecycle_version_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_version_ontology_id ON public.lifecycle_version USING btree (ontology_id);


--
-- Name: ix_lifecycle_version_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_version_release_id ON public.lifecycle_version USING btree (release_id);


--
-- Name: ix_link_quality_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_quality_ontology_id ON public.link_quality USING btree (ontology_id);


--
-- Name: ix_link_type_from_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_type_from_code ON public.link_type USING btree (from_code);


--
-- Name: ix_link_type_from_object_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_type_from_object_id ON public.link_type USING btree (from_object_id);


--
-- Name: ix_link_type_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_type_ontology_id ON public.link_type USING btree (ontology_id);


--
-- Name: ix_link_type_to_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_type_to_code ON public.link_type USING btree (to_code);


--
-- Name: ix_link_type_to_object_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_type_to_object_id ON public.link_type USING btree (to_object_id);


--
-- Name: ix_metric_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_metric_ontology_id ON public.metric USING btree (ontology_id);


--
-- Name: ix_notification_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_ontology_id ON public.notification USING btree (ontology_id);


--
-- Name: ix_notification_recipient_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_recipient_recent ON public.notification USING btree (ontology_id, recipient_id, created_at, id);


--
-- Name: ix_notification_recipient_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_recipient_unread ON public.notification USING btree (ontology_id, recipient_id, read_at);


--
-- Name: ix_object_type_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_object_type_ontology_id ON public.object_type USING btree (ontology_id);


--
-- Name: ix_object_view_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_object_view_code ON public.object_view USING btree (code);


--
-- Name: ix_object_view_object_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_object_view_object_type_id ON public.object_view USING btree (object_type_id);


--
-- Name: ix_object_view_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_object_view_ontology_id ON public.object_view USING btree (ontology_id);


--
-- Name: ix_onto_qa_path_example_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onto_qa_path_example_ontology_id ON public.onto_qa_path_example USING btree (ontology_id);


--
-- Name: ix_onto_qa_path_example_path_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onto_qa_path_example_path_id ON public.onto_qa_path_example USING btree (path_id);


--
-- Name: ix_onto_qa_path_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onto_qa_path_ontology_id ON public.onto_qa_path USING btree (ontology_id);


--
-- Name: ix_onto_qa_path_step_link_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onto_qa_path_step_link_type_id ON public.onto_qa_path_step USING btree (link_type_id);


--
-- Name: ix_onto_qa_path_step_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_onto_qa_path_step_ontology_id ON public.onto_qa_path_step USING btree (ontology_id);


--
-- Name: ix_ontology_edit_rule_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ontology_edit_rule_ontology_id ON public.ontology_edit_rule USING btree (ontology_id);


--
-- Name: ix_ontology_release_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ontology_release_ontology_id ON public.ontology_release USING btree (ontology_id);


--
-- Name: ix_ontology_release_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ontology_release_published_at ON public.ontology_release USING btree (published_at);


--
-- Name: ix_order_line_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_order_line_ontology_id ON public.order_line USING btree (ontology_id);


--
-- Name: ix_order_line_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_order_line_order_id ON public.order_line USING btree (order_id);


--
-- Name: ix_order_line_product_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_order_line_product_id ON public.order_line USING btree (product_id);


--
-- Name: ix_outbox_event_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_event_ontology_id ON public.outbox_event USING btree (ontology_id);


--
-- Name: ix_outbox_event_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_event_pending ON public.outbox_event USING btree (available_at) WHERE (dispatched_at IS NULL);


--
-- Name: ix_outbox_event_topic; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_event_topic ON public.outbox_event USING btree (topic);


--
-- Name: ix_product_barcode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_product_barcode ON public.product USING btree (barcode);


--
-- Name: ix_product_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_product_category ON public.product USING btree (category);


--
-- Name: ix_product_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_product_ontology_id ON public.product USING btree (ontology_id);


--
-- Name: ix_property_object_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_property_object_type_id ON public.property USING btree (object_type_id);


--
-- Name: ix_property_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_property_ontology_id ON public.property USING btree (ontology_id);


--
-- Name: ix_qa_cert_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_cert_ontology_id ON public.qa_cert USING btree (ontology_id);


--
-- Name: ix_qa_cert_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_cert_status ON public.qa_cert USING btree (status);


--
-- Name: ix_qa_cert_variant_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_cert_variant_ontology_id ON public.qa_cert_variant USING btree (ontology_id);


--
-- Name: ix_qa_eval_fail_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_fail_ontology_id ON public.qa_eval_fail USING btree (ontology_id);


--
-- Name: ix_qa_eval_fail_run_id_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_fail_run_id_kind ON public.qa_eval_fail USING btree (run_id, kind);


--
-- Name: ix_qa_eval_question_kind_group_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_question_kind_group_name ON public.qa_eval_question USING btree (kind, group_name);


--
-- Name: ix_qa_eval_question_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_question_ontology_id ON public.qa_eval_question USING btree (ontology_id);


--
-- Name: ix_qa_eval_run_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_run_ontology_id ON public.qa_eval_run USING btree (ontology_id);


--
-- Name: ix_qa_eval_run_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_run_release_id ON public.qa_eval_run USING btree (release_id);


--
-- Name: ix_qa_eval_run_run_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_eval_run_run_at ON public.qa_eval_run USING btree (run_at);


--
-- Name: ix_qa_favorite_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_favorite_ontology_id ON public.qa_favorite USING btree (ontology_id);


--
-- Name: ix_qa_favorite_owner_id_uses; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_favorite_owner_id_uses ON public.qa_favorite USING btree (owner_id, uses);


--
-- Name: ix_qa_feedback_fix_text; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_feedback_fix_text ON public.qa_feedback USING btree (fix_text);


--
-- Name: ix_qa_feedback_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_feedback_ontology_id ON public.qa_feedback USING btree (ontology_id);


--
-- Name: ix_qa_feedback_status_fed_back_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_feedback_status_fed_back_at ON public.qa_feedback USING btree (status, fed_back_at);


--
-- Name: ix_qa_freq_question_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_freq_question_ontology_id ON public.qa_freq_question USING btree (ontology_id);


--
-- Name: ix_qa_object_type_view_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_object_type_view_ontology_id ON public.qa_object_type_view USING btree (ontology_id);


--
-- Name: ix_qa_object_type_view_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_object_type_view_release_id ON public.qa_object_type_view USING btree (release_id);


--
-- Name: ix_qa_page_card_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_page_card_ontology_id ON public.qa_page_card USING btree (ontology_id);


--
-- Name: ix_qa_page_next_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_page_next_run ON public.qa_page USING btree (next_run);


--
-- Name: ix_qa_page_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_page_ontology_id ON public.qa_page USING btree (ontology_id);


--
-- Name: ix_qa_page_owner_id_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_page_owner_id_updated_at ON public.qa_page USING btree (owner_id, updated_at);


--
-- Name: ix_qa_page_share_member_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_page_share_member_ontology_id ON public.qa_page_share_member USING btree (ontology_id);


--
-- Name: ix_qa_page_subscription_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_page_subscription_ontology_id ON public.qa_page_subscription USING btree (ontology_id);


--
-- Name: ix_qa_permission_request_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_permission_request_ontology_id ON public.qa_permission_request USING btree (ontology_id);


--
-- Name: ix_qa_permission_request_requester_id_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_permission_request_requester_id_status ON public.qa_permission_request USING btree (requester_id, status);


--
-- Name: ix_qa_query_log_asked_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_query_log_asked_at ON public.qa_query_log USING btree (asked_at);


--
-- Name: ix_qa_query_log_norm_question_asked_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_query_log_norm_question_asked_at ON public.qa_query_log USING btree (norm_question, asked_at);


--
-- Name: ix_qa_query_log_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_query_log_ontology_id ON public.qa_query_log USING btree (ontology_id);


--
-- Name: ix_qa_session_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_session_ontology_id ON public.qa_session USING btree (ontology_id);


--
-- Name: ix_qa_session_option_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_session_option_ontology_id ON public.qa_session_option USING btree (ontology_id);


--
-- Name: ix_qa_session_owner_id_last_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_session_owner_id_last_at ON public.qa_session USING btree (owner_id, last_at);


--
-- Name: ix_qa_synonym_link_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_synonym_link_ontology_id ON public.qa_synonym_link USING btree (ontology_id);


--
-- Name: ix_qa_template_card_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_template_card_ontology_id ON public.qa_template_card USING btree (ontology_id);


--
-- Name: ix_qa_template_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_template_ontology_id ON public.qa_template USING btree (ontology_id);


--
-- Name: ix_qa_template_scene; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_template_scene ON public.qa_template USING btree (scene);


--
-- Name: ix_qa_turn_event_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_turn_event_ontology_id ON public.qa_turn_event USING btree (ontology_id);


--
-- Name: ix_qa_turn_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_turn_ontology_id ON public.qa_turn USING btree (ontology_id);


--
-- Name: ix_qa_turn_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_qa_turn_release_id ON public.qa_turn USING btree (release_id);


--
-- Name: ix_quality_check_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_check_ontology_id ON public.quality_check USING btree (ontology_id);


--
-- Name: ix_quality_run_check_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_run_check_id ON public.quality_run USING btree (check_id);


--
-- Name: ix_quality_run_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_run_ontology_id ON public.quality_run USING btree (ontology_id);


--
-- Name: ix_quality_schedule_cursor_next_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_schedule_cursor_next_at ON public.quality_schedule_cursor USING btree (next_at);


--
-- Name: ix_quality_schedule_cursor_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_schedule_cursor_ontology_id ON public.quality_schedule_cursor USING btree (ontology_id);


--
-- Name: ix_reconcile_task_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reconcile_task_ontology_id ON public.reconcile_task USING btree (ontology_id);


--
-- Name: ix_reconcile_task_state_next_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reconcile_task_state_next_at ON public.reconcile_task USING btree (state, next_at);


--
-- Name: ix_rule_action_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_action_type_id ON public.rule USING btree (action_type_id);


--
-- Name: ix_rule_draft_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_draft_ontology_id ON public.rule_draft USING btree (ontology_id);


--
-- Name: ix_rule_draft_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_draft_rule_id ON public.rule_draft USING btree (rule_id);


--
-- Name: ix_rule_hit_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_hit_alert_id ON public.rule_hit USING btree (alert_id);


--
-- Name: ix_rule_hit_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_hit_at ON public.rule_hit USING btree (at);


--
-- Name: ix_rule_hit_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_hit_ontology_id ON public.rule_hit USING btree (ontology_id);


--
-- Name: ix_rule_hit_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_hit_rule_id ON public.rule_hit USING btree (rule_id);


--
-- Name: ix_rule_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_ontology_id ON public.rule USING btree (ontology_id);


--
-- Name: ix_rule_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_release_id ON public.rule USING btree (release_id);


--
-- Name: ix_rule_schedule_cursor_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_schedule_cursor_ontology_id ON public.rule_schedule_cursor USING btree (ontology_id);


--
-- Name: ix_rule_schedule_run_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_schedule_run_ontology_id ON public.rule_schedule_run USING btree (ontology_id);


--
-- Name: ix_rule_schedule_run_release_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_schedule_run_release_id ON public.rule_schedule_run USING btree (release_id);


--
-- Name: ix_rule_schedule_run_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_schedule_run_rule_id ON public.rule_schedule_run USING btree (rule_id);


--
-- Name: ix_rule_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_status ON public.rule USING btree (status);


--
-- Name: ix_rule_version_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_version_ontology_id ON public.rule_version USING btree (ontology_id);


--
-- Name: ix_rule_version_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_version_rule_id ON public.rule_version USING btree (rule_id);


--
-- Name: ix_scenario_impact_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scenario_impact_ontology_id ON public.scenario_impact USING btree (ontology_id);


--
-- Name: ix_scenario_impact_scenario_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scenario_impact_scenario_id ON public.scenario_impact USING btree (scenario_id);


--
-- Name: ix_scenario_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scenario_ontology_id ON public.scenario USING btree (ontology_id);


--
-- Name: ix_scenario_result_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scenario_result_ontology_id ON public.scenario_result USING btree (ontology_id);


--
-- Name: ix_scenario_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scenario_status ON public.scenario USING btree (status);


--
-- Name: ix_scenario_template_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scenario_template_ontology_id ON public.scenario_template USING btree (ontology_id);


--
-- Name: ix_side_effect_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_side_effect_ontology_id ON public.side_effect USING btree (ontology_id);


--
-- Name: ix_store_big_region; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_store_big_region ON public.store USING btree (big_region);


--
-- Name: ix_store_city; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_store_city ON public.store USING btree (city);


--
-- Name: ix_store_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_store_ontology_id ON public.store USING btree (ontology_id);


--
-- Name: ix_store_region; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_store_region ON public.store USING btree (region);


--
-- Name: ix_submission_criterion_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_submission_criterion_ontology_id ON public.submission_criterion USING btree (ontology_id);


--
-- Name: ix_sys_role_member_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sys_role_member_role_id ON public.sys_role_member USING btree (role_id);


--
-- Name: ix_sys_role_member_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sys_role_member_user_id ON public.sys_role_member USING btree (user_id);


--
-- Name: ix_sys_role_scope_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sys_role_scope_ontology_id ON public.sys_role_scope USING btree (ontology_id);


--
-- Name: ix_sys_role_scope_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sys_role_scope_role_id ON public.sys_role_scope USING btree (role_id);


--
-- Name: ix_sys_user_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sys_user_name ON public.sys_user USING btree (name);


--
-- Name: ix_writeback_call_action_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_writeback_call_action_run_id ON public.writeback_call USING btree (action_run_id);


--
-- Name: ix_writeback_call_ontology_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_writeback_call_ontology_id ON public.writeback_call USING btree (ontology_id);


--
-- Name: ix_writeback_call_sys_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_writeback_call_sys_at ON public.writeback_call USING btree (sys_id, at);


--
-- Name: procrastinate_events_job_id_fkey_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_events_job_id_fkey_v1 ON public.procrastinate_events USING btree (job_id);


--
-- Name: procrastinate_jobs_id_lock_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_jobs_id_lock_idx_v1 ON public.procrastinate_jobs USING btree (id, lock) WHERE (status = ANY (ARRAY['todo'::public.procrastinate_job_status, 'doing'::public.procrastinate_job_status]));


--
-- Name: procrastinate_jobs_lock_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX procrastinate_jobs_lock_idx_v1 ON public.procrastinate_jobs USING btree (lock) WHERE (status = 'doing'::public.procrastinate_job_status);


--
-- Name: procrastinate_jobs_priority_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_jobs_priority_idx_v1 ON public.procrastinate_jobs USING btree (priority DESC, id) WHERE (status = 'todo'::public.procrastinate_job_status);


--
-- Name: procrastinate_jobs_queue_name_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_jobs_queue_name_idx_v1 ON public.procrastinate_jobs USING btree (queue_name);


--
-- Name: procrastinate_jobs_queueing_lock_idx_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX procrastinate_jobs_queueing_lock_idx_v1 ON public.procrastinate_jobs USING btree (queueing_lock) WHERE (status = 'todo'::public.procrastinate_job_status);


--
-- Name: procrastinate_periodic_defers_job_id_fkey_v1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX procrastinate_periodic_defers_job_id_fkey_v1 ON public.procrastinate_periodic_defers USING btree (job_id);


--
-- Name: uq_action_param_action_type_id_api_nonseed; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_action_param_action_type_id_api_nonseed ON public.action_param USING btree (ontology_id, action_type_id, api) WHERE (NOT is_seed);


--
-- Name: uq_action_run_idem_key_nonseed; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_action_run_idem_key_nonseed ON public.action_run USING btree (ontology_id, idem_key) WHERE (NOT is_seed);


--
-- Name: uq_api_key_live_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_api_key_live_name ON public.api_key USING btree (name) WHERE (revoked_at IS NULL);


--
-- Name: uq_biz_order_code_nonseed; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_biz_order_code_nonseed ON public.biz_order USING btree (ontology_id, code) WHERE (NOT is_seed);


--
-- Name: uq_object_view_code_nonseed; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_object_view_code_nonseed ON public.object_view USING btree (ontology_id, code) WHERE (NOT is_seed);


--
-- Name: uq_onto_qa_path_default; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_onto_qa_path_default ON public.onto_qa_path USING btree (ontology_id, from_object_id, to_object_id) WHERE is_default;


--
-- Name: uq_qa_cert_lower_question; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_qa_cert_lower_question ON public.qa_cert USING btree (ontology_id, lower((question)::text));


--
-- Name: uq_qa_cert_variant_lower_question; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_qa_cert_variant_lower_question ON public.qa_cert_variant USING btree (ontology_id, lower((question)::text));


--
-- Name: uq_qa_eval_run_active_job; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_qa_eval_run_active_job ON public.qa_eval_run USING btree (ontology_id, (1)) WHERE ((queue_job_id IS NOT NULL) AND ((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text])));


--
-- Name: procrastinate_jobs procrastinate_jobs_notify_queue_job_aborted_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_jobs_notify_queue_job_aborted_v1 AFTER UPDATE OF abort_requested ON public.procrastinate_jobs FOR EACH ROW WHEN (((old.abort_requested = false) AND (new.abort_requested = true) AND (new.status = 'doing'::public.procrastinate_job_status))) EXECUTE FUNCTION public.procrastinate_notify_queue_abort_job_v1();


--
-- Name: procrastinate_jobs procrastinate_jobs_notify_queue_job_inserted_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_jobs_notify_queue_job_inserted_v1 AFTER INSERT ON public.procrastinate_jobs FOR EACH ROW WHEN ((new.status = 'todo'::public.procrastinate_job_status)) EXECUTE FUNCTION public.procrastinate_notify_queue_job_inserted_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_abort_requested_events_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_abort_requested_events_v1 AFTER UPDATE OF abort_requested ON public.procrastinate_jobs FOR EACH ROW WHEN ((new.abort_requested = true)) EXECUTE FUNCTION public.procrastinate_trigger_abort_requested_events_procedure_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_delete_jobs_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_delete_jobs_v1 BEFORE DELETE ON public.procrastinate_jobs FOR EACH ROW EXECUTE FUNCTION public.procrastinate_unlink_periodic_defers_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_scheduled_events_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_scheduled_events_v1 AFTER INSERT OR UPDATE ON public.procrastinate_jobs FOR EACH ROW WHEN (((new.scheduled_at IS NOT NULL) AND (new.status = 'todo'::public.procrastinate_job_status))) EXECUTE FUNCTION public.procrastinate_trigger_function_scheduled_events_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_status_events_insert_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_status_events_insert_v1 AFTER INSERT ON public.procrastinate_jobs FOR EACH ROW WHEN ((new.status = 'todo'::public.procrastinate_job_status)) EXECUTE FUNCTION public.procrastinate_trigger_function_status_events_insert_v1();


--
-- Name: procrastinate_jobs procrastinate_trigger_status_events_update_v1; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER procrastinate_trigger_status_events_update_v1 AFTER UPDATE OF status ON public.procrastinate_jobs FOR EACH ROW EXECUTE FUNCTION public.procrastinate_trigger_function_status_events_update_v1();


--
-- Name: action_param trg_action_param_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_param_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.action_param FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: action_param trg_action_param_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_param_scope_immutable BEFORE UPDATE ON public.action_param FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_param');


--
-- Name: action_run trg_action_run_alert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_alert_id_ontology BEFORE INSERT OR UPDATE ON public.action_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('alert_id', 'alert');


--
-- Name: action_run_daily_counter trg_action_run_daily_counter_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_daily_counter_scope_immutable BEFORE UPDATE ON public.action_run_daily_counter FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_run_daily_counter');


--
-- Name: action_run_daily_stat trg_action_run_daily_stat_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_daily_stat_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.action_run_daily_stat FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: action_run_daily_stat trg_action_run_daily_stat_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_daily_stat_scope_immutable BEFORE UPDATE ON public.action_run_daily_stat FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_run_daily_stat');


--
-- Name: action_run_log trg_action_run_log_action_run_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_log_action_run_id_ontology BEFORE INSERT OR UPDATE ON public.action_run_log FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_run_id', 'action_run');


--
-- Name: action_run_log trg_action_run_log_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_log_append_only BEFORE DELETE OR UPDATE ON public.action_run_log FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: action_run_log trg_action_run_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_log_no_truncate BEFORE TRUNCATE ON public.action_run_log FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: action_run_log trg_action_run_log_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_log_scope_immutable BEFORE UPDATE ON public.action_run_log FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_run_log');


--
-- Name: action_run trg_action_run_retried_as_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_retried_as_id_ontology BEFORE INSERT OR UPDATE ON public.action_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('retried_as_id', 'action_run');


--
-- Name: action_run trg_action_run_retry_of_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_retry_of_id_ontology BEFORE INSERT OR UPDATE ON public.action_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('retry_of_id', 'action_run');


--
-- Name: action_run trg_action_run_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_run_scope_immutable BEFORE UPDATE ON public.action_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_run');


--
-- Name: action_type trg_action_type_retired_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_retired_release_id_ontology BEFORE INSERT OR UPDATE ON public.action_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('retired_release_id', 'ontology_release');


--
-- Name: action_type_rule trg_action_type_rule_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_rule_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.action_type_rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: action_type_rule trg_action_type_rule_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_rule_scope_immutable BEFORE UPDATE ON public.action_type_rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_type_rule');


--
-- Name: action_type trg_action_type_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_scope_immutable BEFORE UPDATE ON public.action_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_type');


--
-- Name: action_type trg_action_type_sys_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_sys_id_ontology BEFORE INSERT OR UPDATE ON public.action_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('sys_id', 'ext_system');


--
-- Name: action_type_version trg_action_type_version_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_version_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.action_type_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: action_type_version trg_action_type_version_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_version_append_only BEFORE DELETE OR UPDATE ON public.action_type_version FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: action_type_version trg_action_type_version_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_version_no_truncate BEFORE TRUNCATE ON public.action_type_version FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: action_type_version trg_action_type_version_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_action_type_version_scope_immutable BEFORE UPDATE ON public.action_type_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'action_type_version');


--
-- Name: alert trg_alert_action_run_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_action_run_id_ontology BEFORE INSERT OR UPDATE ON public.alert FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_run_id', 'action_run');


--
-- Name: alert_log trg_alert_log_alert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_log_alert_id_ontology BEFORE INSERT OR UPDATE ON public.alert_log FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('alert_id', 'alert');


--
-- Name: alert_log trg_alert_log_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_log_append_only BEFORE DELETE OR UPDATE ON public.alert_log FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: alert_log trg_alert_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_log_no_truncate BEFORE TRUNCATE ON public.alert_log FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: alert_log trg_alert_log_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_log_scope_immutable BEFORE UPDATE ON public.alert_log FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'alert_log');


--
-- Name: alert trg_alert_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_alert_scope_immutable BEFORE UPDATE ON public.alert FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'alert');


--
-- Name: audit_export_job trg_audit_export_job_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_export_job_append_only BEFORE DELETE OR UPDATE ON public.audit_export_job FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: audit_export_job trg_audit_export_job_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_export_job_no_truncate BEFORE TRUNCATE ON public.audit_export_job FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: audit_export_row trg_audit_export_row_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_export_row_append_only BEFORE DELETE OR UPDATE ON public.audit_export_row FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: audit_export_row trg_audit_export_row_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_export_row_no_truncate BEFORE TRUNCATE ON public.audit_export_row FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: audit_log trg_audit_log_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_log_append_only BEFORE DELETE OR UPDATE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: audit_log trg_audit_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_log_no_truncate BEFORE TRUNCATE ON public.audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: biz_order trg_biz_order_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_biz_order_scope_immutable BEFORE UPDATE ON public.biz_order FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'biz_order');


--
-- Name: biz_order trg_biz_order_store_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_biz_order_store_id_ontology BEFORE INSERT OR UPDATE ON public.biz_order FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('store_id', 'store');


--
-- Name: change_request trg_change_request_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_change_request_release_id_ontology BEFORE INSERT OR UPDATE ON public.change_request FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: change_request trg_change_request_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_change_request_scope_immutable BEFORE UPDATE ON public.change_request FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'change_request');


--
-- Name: cr_check trg_cr_check_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_check_cr_id_ontology BEFORE INSERT OR UPDATE ON public.cr_check FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: cr_check trg_cr_check_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_check_scope_immutable BEFORE UPDATE ON public.cr_check FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'cr_check');


--
-- Name: cr_event trg_cr_event_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_event_cr_id_ontology BEFORE INSERT OR UPDATE ON public.cr_event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: cr_event trg_cr_event_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_event_scope_immutable BEFORE UPDATE ON public.cr_event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'cr_event');


--
-- Name: cr_impact trg_cr_impact_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_impact_cr_id_ontology BEFORE INSERT OR UPDATE ON public.cr_impact FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: cr_impact trg_cr_impact_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_impact_scope_immutable BEFORE UPDATE ON public.cr_impact FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'cr_impact');


--
-- Name: cr_item trg_cr_item_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_item_cr_id_ontology BEFORE INSERT OR UPDATE ON public.cr_item FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: cr_item trg_cr_item_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_item_scope_immutable BEFORE UPDATE ON public.cr_item FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'cr_item');


--
-- Name: cr_target trg_cr_target_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_target_cr_id_ontology BEFORE INSERT OR UPDATE ON public.cr_target FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: cr_target trg_cr_target_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_target_scope_immutable BEFORE UPDATE ON public.cr_target FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'cr_target');


--
-- Name: cr_trial trg_cr_trial_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_trial_cr_id_ontology BEFORE INSERT OR UPDATE ON public.cr_trial FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: cr_trial trg_cr_trial_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cr_trial_scope_immutable BEFORE UPDATE ON public.cr_trial FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'cr_trial');


--
-- Name: datasource_run trg_datasource_run_datasource_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datasource_run_datasource_id_ontology BEFORE INSERT OR UPDATE ON public.datasource_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('datasource_id', 'datasource');


--
-- Name: datasource_run trg_datasource_run_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datasource_run_scope_immutable BEFORE UPDATE ON public.datasource_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'datasource_run');


--
-- Name: datasource trg_datasource_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datasource_scope_immutable BEFORE UPDATE ON public.datasource FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'datasource');


--
-- Name: datasource_table trg_datasource_table_datasource_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datasource_table_datasource_id_ontology BEFORE INSERT OR UPDATE ON public.datasource_table FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('datasource_id', 'datasource');


--
-- Name: datasource_table trg_datasource_table_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datasource_table_scope_immutable BEFORE UPDATE ON public.datasource_table FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'datasource_table');


--
-- Name: decision_log trg_decision_log_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_decision_log_append_only BEFORE DELETE OR UPDATE ON public.decision_log FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: decision_log trg_decision_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_decision_log_no_truncate BEFORE TRUNCATE ON public.decision_log FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: dim_level trg_dim_level_dimension_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dim_level_dimension_id_ontology BEFORE INSERT OR UPDATE ON public.dim_level FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('dimension_id', 'dimension');


--
-- Name: dim_level trg_dim_level_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dim_level_scope_immutable BEFORE UPDATE ON public.dim_level FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'dim_level');


--
-- Name: dim_mapping trg_dim_mapping_dimension_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dim_mapping_dimension_id_ontology BEFORE INSERT OR UPDATE ON public.dim_mapping FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('dimension_id', 'dimension');


--
-- Name: dim_mapping trg_dim_mapping_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dim_mapping_scope_immutable BEFORE UPDATE ON public.dim_mapping FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'dim_mapping');


--
-- Name: dim_value trg_dim_value_dimension_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dim_value_dimension_id_ontology BEFORE INSERT OR UPDATE ON public.dim_value FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('dimension_id', 'dimension');


--
-- Name: dim_value trg_dim_value_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dim_value_scope_immutable BEFORE UPDATE ON public.dim_value FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'dim_value');


--
-- Name: dimension trg_dimension_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dimension_object_id_ontology BEFORE INSERT OR UPDATE ON public.dimension FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('object_id', 'object_type');


--
-- Name: dimension trg_dimension_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dimension_scope_immutable BEFORE UPDATE ON public.dimension FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'dimension');


--
-- Name: event_daily_stat trg_event_daily_stat_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_daily_stat_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.event_daily_stat FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: event_daily_stat trg_event_daily_stat_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_daily_stat_scope_immutable BEFORE UPDATE ON public.event_daily_stat FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_daily_stat');


--
-- Name: event_dedup trg_event_dedup_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_dedup_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.event_dedup FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: event_dedup trg_event_dedup_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_dedup_scope_immutable BEFORE UPDATE ON public.event_dedup FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_dedup');


--
-- Name: event_delivery trg_event_delivery_event_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_delivery_event_id_ontology BEFORE INSERT OR UPDATE ON public.event_delivery FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_id', 'event');


--
-- Name: event_delivery_record trg_event_delivery_record_delivery_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_delivery_record_delivery_id_ontology BEFORE INSERT OR UPDATE ON public.event_delivery_record FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('delivery_id', 'event_delivery');


--
-- Name: event_delivery_record trg_event_delivery_record_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_delivery_record_scope_immutable BEFORE UPDATE ON public.event_delivery_record FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_delivery_record');


--
-- Name: event_delivery trg_event_delivery_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_delivery_scope_immutable BEFORE UPDATE ON public.event_delivery FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_delivery');


--
-- Name: event_envelope_field trg_event_envelope_field_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_envelope_field_scope_immutable BEFORE UPDATE ON public.event_envelope_field FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_envelope_field');


--
-- Name: event trg_event_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: event_payload_field trg_event_payload_field_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_payload_field_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.event_payload_field FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: event_payload_field trg_event_payload_field_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_payload_field_scope_immutable BEFORE UPDATE ON public.event_payload_field FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_payload_field');


--
-- Name: event trg_event_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_release_id_ontology BEFORE INSERT OR UPDATE ON public.event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: event trg_event_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_scope_immutable BEFORE UPDATE ON public.event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event');


--
-- Name: event_subscription trg_event_subscription_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_subscription_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.event_subscription FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: event_subscription trg_event_subscription_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_subscription_scope_immutable BEFORE UPDATE ON public.event_subscription FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_subscription');


--
-- Name: event_type trg_event_type_retired_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_type_retired_release_id_ontology BEFORE INSERT OR UPDATE ON public.event_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('retired_release_id', 'ontology_release');


--
-- Name: event_type trg_event_type_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_type_scope_immutable BEFORE UPDATE ON public.event_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_type');


--
-- Name: event_type_version trg_event_type_version_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_type_version_append_only BEFORE DELETE OR UPDATE ON public.event_type_version FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: event_type_version trg_event_type_version_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_type_version_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.event_type_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: event_type_version trg_event_type_version_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_type_version_no_truncate BEFORE TRUNCATE ON public.event_type_version FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: event_type_version trg_event_type_version_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_event_type_version_scope_immutable BEFORE UPDATE ON public.event_type_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'event_type_version');


--
-- Name: ext_system trg_ext_system_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ext_system_scope_immutable BEFORE UPDATE ON public.ext_system FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'ext_system');


--
-- Name: fn_param trg_fn_param_function_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_param_function_id_ontology BEFORE INSERT OR UPDATE ON public.fn_param FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('function_id', 'function');


--
-- Name: fn_param trg_fn_param_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_param_scope_immutable BEFORE UPDATE ON public.fn_param FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'fn_param');


--
-- Name: fn_use trg_fn_use_function_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_use_function_id_ontology BEFORE INSERT OR UPDATE ON public.fn_use FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('function_id', 'function');


--
-- Name: fn_use trg_fn_use_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_use_scope_immutable BEFORE UPDATE ON public.fn_use FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'fn_use');


--
-- Name: fn_version trg_fn_version_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_version_append_only BEFORE DELETE OR UPDATE ON public.fn_version FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: fn_version trg_fn_version_function_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_version_function_id_ontology BEFORE INSERT OR UPDATE ON public.fn_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('function_id', 'function');


--
-- Name: fn_version trg_fn_version_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_version_no_truncate BEFORE TRUNCATE ON public.fn_version FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: fn_version trg_fn_version_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fn_version_scope_immutable BEFORE UPDATE ON public.fn_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'fn_version');


--
-- Name: function trg_function_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_function_scope_immutable BEFORE UPDATE ON public.function FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'function');


--
-- Name: knowledge_apply trg_knowledge_apply_knowledge_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_knowledge_apply_knowledge_id_ontology BEFORE INSERT OR UPDATE ON public.knowledge_apply FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('knowledge_id', 'knowledge');


--
-- Name: knowledge_apply trg_knowledge_apply_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_knowledge_apply_scope_immutable BEFORE UPDATE ON public.knowledge_apply FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'knowledge_apply');


--
-- Name: knowledge trg_knowledge_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_knowledge_scope_immutable BEFORE UPDATE ON public.knowledge FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'knowledge');


--
-- Name: lifecycle_change trg_lifecycle_change_alert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_change_alert_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_change FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('alert_id', 'alert');


--
-- Name: lifecycle_change trg_lifecycle_change_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_change_append_only BEFORE DELETE OR UPDATE ON public.lifecycle_change FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: lifecycle_change trg_lifecycle_change_lifecycle_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_change_lifecycle_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_change FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('lifecycle_id', 'lifecycle');


--
-- Name: lifecycle_change trg_lifecycle_change_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_change_no_truncate BEFORE TRUNCATE ON public.lifecycle_change FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: lifecycle_change trg_lifecycle_change_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_change_scope_immutable BEFORE UPDATE ON public.lifecycle_change FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'lifecycle_change');


--
-- Name: lifecycle_change trg_lifecycle_change_transition_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_change_transition_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_change FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('transition_id', 'lifecycle_transition');


--
-- Name: lifecycle_draft trg_lifecycle_draft_lifecycle_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_draft_lifecycle_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_draft FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('lifecycle_id', 'lifecycle');


--
-- Name: lifecycle_draft trg_lifecycle_draft_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_draft_scope_immutable BEFORE UPDATE ON public.lifecycle_draft FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'lifecycle_draft');


--
-- Name: lifecycle trg_lifecycle_object_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_object_type_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('object_type_id', 'object_type');


--
-- Name: lifecycle trg_lifecycle_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_scope_immutable BEFORE UPDATE ON public.lifecycle FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'lifecycle');


--
-- Name: lifecycle_state trg_lifecycle_state_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_state_scope_immutable BEFORE UPDATE ON public.lifecycle_state FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'lifecycle_state');


--
-- Name: lifecycle_state trg_lifecycle_state_version_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_state_version_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_state FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('version_id', 'lifecycle_version');


--
-- Name: lifecycle_transition trg_lifecycle_transition_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_transition_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_transition FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: lifecycle_transition trg_lifecycle_transition_event_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_transition_event_type_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_transition FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('event_type_id', 'event_type');


--
-- Name: lifecycle_transition trg_lifecycle_transition_from_state_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_transition_from_state_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_transition FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('from_state_id', 'lifecycle_state');


--
-- Name: lifecycle_transition trg_lifecycle_transition_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_transition_scope_immutable BEFORE UPDATE ON public.lifecycle_transition FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'lifecycle_transition');


--
-- Name: lifecycle_transition trg_lifecycle_transition_to_state_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_transition_to_state_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_transition FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('to_state_id', 'lifecycle_state');


--
-- Name: lifecycle_transition trg_lifecycle_transition_version_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_transition_version_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_transition FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('version_id', 'lifecycle_version');


--
-- Name: lifecycle_version trg_lifecycle_version_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_version_append_only BEFORE DELETE OR UPDATE ON public.lifecycle_version FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: lifecycle_version trg_lifecycle_version_lifecycle_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_version_lifecycle_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('lifecycle_id', 'lifecycle');


--
-- Name: lifecycle_version trg_lifecycle_version_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_version_no_truncate BEFORE TRUNCATE ON public.lifecycle_version FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: lifecycle_version trg_lifecycle_version_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_version_release_id_ontology BEFORE INSERT OR UPDATE ON public.lifecycle_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: lifecycle_version trg_lifecycle_version_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_lifecycle_version_scope_immutable BEFORE UPDATE ON public.lifecycle_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'lifecycle_version');


--
-- Name: link_quality trg_link_quality_link_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_link_quality_link_type_id_ontology BEFORE INSERT OR UPDATE ON public.link_quality FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('link_type_id', 'link_type');


--
-- Name: link_quality trg_link_quality_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_link_quality_scope_immutable BEFORE UPDATE ON public.link_quality FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'link_quality');


--
-- Name: link_type trg_link_type_from_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_link_type_from_object_id_ontology BEFORE INSERT OR UPDATE ON public.link_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('from_object_id', 'object_type');


--
-- Name: link_type trg_link_type_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_link_type_scope_immutable BEFORE UPDATE ON public.link_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'link_type');


--
-- Name: link_type trg_link_type_to_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_link_type_to_object_id_ontology BEFORE INSERT OR UPDATE ON public.link_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('to_object_id', 'object_type');


--
-- Name: metric trg_metric_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_metric_scope_immutable BEFORE UPDATE ON public.metric FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'metric');


--
-- Name: notification trg_notification_cr_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notification_cr_id_ontology BEFORE INSERT OR UPDATE ON public.notification FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cr_id', 'change_request');


--
-- Name: notification trg_notification_source_event_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notification_source_event_id_ontology BEFORE INSERT OR UPDATE ON public.notification FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('source_event_id', 'cr_event');


--
-- Name: object_type trg_object_type_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_object_type_scope_immutable BEFORE UPDATE ON public.object_type FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'object_type');


--
-- Name: object_view trg_object_view_object_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_object_view_object_type_id_ontology BEFORE INSERT OR UPDATE ON public.object_view FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('object_type_id', 'object_type');


--
-- Name: object_view trg_object_view_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_object_view_scope_immutable BEFORE UPDATE ON public.object_view FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'object_view');


--
-- Name: onto_qa_path_example trg_onto_qa_path_example_from_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_example_from_object_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path_example FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('from_object_id', 'object_type');


--
-- Name: onto_qa_path_example trg_onto_qa_path_example_path_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_example_path_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path_example FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('path_id', 'onto_qa_path');


--
-- Name: onto_qa_path_example trg_onto_qa_path_example_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_example_scope_immutable BEFORE UPDATE ON public.onto_qa_path_example FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'onto_qa_path_example');


--
-- Name: onto_qa_path_example trg_onto_qa_path_example_to_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_example_to_object_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path_example FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('to_object_id', 'object_type');


--
-- Name: onto_qa_path trg_onto_qa_path_from_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_from_object_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('from_object_id', 'object_type');


--
-- Name: onto_qa_path trg_onto_qa_path_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_scope_immutable BEFORE UPDATE ON public.onto_qa_path FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'onto_qa_path');


--
-- Name: onto_qa_path_step trg_onto_qa_path_step_link_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_step_link_type_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path_step FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('link_type_id', 'link_type');


--
-- Name: onto_qa_path_step trg_onto_qa_path_step_path_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_step_path_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path_step FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('path_id', 'onto_qa_path');


--
-- Name: onto_qa_path_step trg_onto_qa_path_step_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_step_scope_immutable BEFORE UPDATE ON public.onto_qa_path_step FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'onto_qa_path_step');


--
-- Name: onto_qa_path trg_onto_qa_path_to_object_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_onto_qa_path_to_object_id_ontology BEFORE INSERT OR UPDATE ON public.onto_qa_path FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('to_object_id', 'object_type');


--
-- Name: ontology_edit_rule trg_ontology_edit_rule_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_edit_rule_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.ontology_edit_rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: ontology_edit_rule trg_ontology_edit_rule_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_edit_rule_scope_immutable BEFORE UPDATE ON public.ontology_edit_rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'ontology_edit_rule');


--
-- Name: ontology_release trg_ontology_release_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_release_append_only BEFORE DELETE OR UPDATE ON public.ontology_release FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: ontology_release trg_ontology_release_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_release_no_truncate BEFORE TRUNCATE ON public.ontology_release FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: ontology_release trg_ontology_release_parent_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_release_parent_id_ontology BEFORE INSERT OR UPDATE ON public.ontology_release FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('parent_id', 'ontology_release');


--
-- Name: ontology_release trg_ontology_release_rollback_of_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_release_rollback_of_id_ontology BEFORE INSERT OR UPDATE ON public.ontology_release FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('rollback_of_id', 'ontology_release');


--
-- Name: ontology_release trg_ontology_release_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ontology_release_scope_immutable BEFORE UPDATE ON public.ontology_release FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'ontology_release');


--
-- Name: order_line trg_order_line_order_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_order_line_order_id_ontology BEFORE INSERT OR UPDATE ON public.order_line FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('order_id', 'biz_order');


--
-- Name: order_line trg_order_line_product_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_order_line_product_id_ontology BEFORE INSERT OR UPDATE ON public.order_line FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('product_id', 'product');


--
-- Name: order_line trg_order_line_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_order_line_scope_immutable BEFORE UPDATE ON public.order_line FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'order_line');


--
-- Name: outbox_event trg_outbox_event_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_outbox_event_scope_immutable BEFORE UPDATE ON public.outbox_event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'outbox_event');


--
-- Name: product trg_product_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_product_scope_immutable BEFORE UPDATE ON public.product FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'product');


--
-- Name: property trg_property_object_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_property_object_type_id_ontology BEFORE INSERT OR UPDATE ON public.property FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('object_type_id', 'object_type');


--
-- Name: property trg_property_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_property_scope_immutable BEFORE UPDATE ON public.property FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'property');


--
-- Name: qa_cert trg_qa_cert_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_cert_scope_immutable BEFORE UPDATE ON public.qa_cert FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_cert');


--
-- Name: qa_cert_variant trg_qa_cert_variant_cert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_cert_variant_cert_id_ontology BEFORE INSERT OR UPDATE ON public.qa_cert_variant FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cert_id', 'qa_cert');


--
-- Name: qa_cert_variant trg_qa_cert_variant_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_cert_variant_scope_immutable BEFORE UPDATE ON public.qa_cert_variant FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_cert_variant');


--
-- Name: qa_eval_fail trg_qa_eval_fail_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_fail_append_only BEFORE DELETE OR UPDATE ON public.qa_eval_fail FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: qa_eval_fail trg_qa_eval_fail_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_fail_no_truncate BEFORE TRUNCATE ON public.qa_eval_fail FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: qa_eval_fail trg_qa_eval_fail_question_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_fail_question_id_ontology BEFORE INSERT OR UPDATE ON public.qa_eval_fail FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('question_id', 'qa_eval_question');


--
-- Name: qa_eval_fail trg_qa_eval_fail_run_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_fail_run_id_ontology BEFORE INSERT OR UPDATE ON public.qa_eval_fail FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('run_id', 'qa_eval_run');


--
-- Name: qa_eval_fail trg_qa_eval_fail_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_fail_scope_immutable BEFORE UPDATE ON public.qa_eval_fail FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_eval_fail');


--
-- Name: qa_eval_question trg_qa_eval_question_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_question_scope_immutable BEFORE UPDATE ON public.qa_eval_question FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_eval_question');


--
-- Name: qa_eval_run trg_qa_eval_run_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_run_release_id_ontology BEFORE INSERT OR UPDATE ON public.qa_eval_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: qa_eval_run trg_qa_eval_run_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_eval_run_scope_immutable BEFORE UPDATE ON public.qa_eval_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_eval_run');


--
-- Name: qa_favorite trg_qa_favorite_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_favorite_scope_immutable BEFORE UPDATE ON public.qa_favorite FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_favorite');


--
-- Name: qa_feedback trg_qa_feedback_cert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_feedback_cert_id_ontology BEFORE INSERT OR UPDATE ON public.qa_feedback FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cert_id', 'qa_cert');


--
-- Name: qa_feedback trg_qa_feedback_cert_variant_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_feedback_cert_variant_id_ontology BEFORE INSERT OR UPDATE ON public.qa_feedback FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cert_variant_id', 'qa_cert_variant');


--
-- Name: qa_feedback trg_qa_feedback_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_feedback_scope_immutable BEFORE UPDATE ON public.qa_feedback FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_feedback');


--
-- Name: qa_feedback trg_qa_feedback_turn_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_feedback_turn_id_ontology BEFORE INSERT OR UPDATE ON public.qa_feedback FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('turn_id', 'qa_turn');


--
-- Name: qa_freq_question trg_qa_freq_question_cert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_freq_question_cert_id_ontology BEFORE INSERT OR UPDATE ON public.qa_freq_question FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('cert_id', 'qa_cert');


--
-- Name: qa_freq_question trg_qa_freq_question_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_freq_question_scope_immutable BEFORE UPDATE ON public.qa_freq_question FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_freq_question');


--
-- Name: qa_object_type_view trg_qa_object_type_view_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_object_type_view_append_only BEFORE DELETE OR UPDATE ON public.qa_object_type_view FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: qa_object_type_view trg_qa_object_type_view_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_object_type_view_no_truncate BEFORE TRUNCATE ON public.qa_object_type_view FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: qa_object_type_view trg_qa_object_type_view_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_object_type_view_release_id_ontology BEFORE INSERT OR UPDATE ON public.qa_object_type_view FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: qa_object_type_view trg_qa_object_type_view_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_object_type_view_scope_immutable BEFORE UPDATE ON public.qa_object_type_view FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_object_type_view');


--
-- Name: qa_page_card trg_qa_page_card_page_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_card_page_id_ontology BEFORE INSERT OR UPDATE ON public.qa_page_card FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('page_id', 'qa_page');


--
-- Name: qa_page_card trg_qa_page_card_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_card_scope_immutable BEFORE UPDATE ON public.qa_page_card FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_page_card');


--
-- Name: qa_page trg_qa_page_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_scope_immutable BEFORE UPDATE ON public.qa_page FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_page');


--
-- Name: qa_page_share_member trg_qa_page_share_member_page_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_share_member_page_id_ontology BEFORE INSERT OR UPDATE ON public.qa_page_share_member FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('page_id', 'qa_page');


--
-- Name: qa_page_share_member trg_qa_page_share_member_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_share_member_scope_immutable BEFORE UPDATE ON public.qa_page_share_member FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_page_share_member');


--
-- Name: qa_page_subscription trg_qa_page_subscription_page_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_subscription_page_id_ontology BEFORE INSERT OR UPDATE ON public.qa_page_subscription FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('page_id', 'qa_page');


--
-- Name: qa_page_subscription trg_qa_page_subscription_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_page_subscription_scope_immutable BEFORE UPDATE ON public.qa_page_subscription FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_page_subscription');


--
-- Name: qa_permission_request trg_qa_permission_request_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_permission_request_scope_immutable BEFORE UPDATE ON public.qa_permission_request FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_permission_request');


--
-- Name: qa_permission_request trg_qa_permission_request_source_turn_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_permission_request_source_turn_id_ontology BEFORE INSERT OR UPDATE ON public.qa_permission_request FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('source_turn_id', 'qa_turn');


--
-- Name: qa_query_log trg_qa_query_log_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_query_log_append_only BEFORE DELETE OR UPDATE ON public.qa_query_log FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: qa_query_log trg_qa_query_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_query_log_no_truncate BEFORE TRUNCATE ON public.qa_query_log FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: qa_query_log trg_qa_query_log_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_query_log_scope_immutable BEFORE UPDATE ON public.qa_query_log FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_query_log');


--
-- Name: qa_session_option trg_qa_session_option_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_session_option_scope_immutable BEFORE UPDATE ON public.qa_session_option FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_session_option');


--
-- Name: qa_session trg_qa_session_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_session_scope_immutable BEFORE UPDATE ON public.qa_session FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_session');


--
-- Name: qa_synonym_link trg_qa_synonym_link_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_synonym_link_scope_immutable BEFORE UPDATE ON public.qa_synonym_link FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_synonym_link');


--
-- Name: qa_template_card trg_qa_template_card_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_template_card_scope_immutable BEFORE UPDATE ON public.qa_template_card FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_template_card');


--
-- Name: qa_template_card trg_qa_template_card_template_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_template_card_template_id_ontology BEFORE INSERT OR UPDATE ON public.qa_template_card FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('template_id', 'qa_template');


--
-- Name: qa_template trg_qa_template_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_template_scope_immutable BEFORE UPDATE ON public.qa_template FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_template');


--
-- Name: qa_turn_event trg_qa_turn_event_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_turn_event_scope_immutable BEFORE UPDATE ON public.qa_turn_event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_turn_event');


--
-- Name: qa_turn_event trg_qa_turn_event_turn_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_turn_event_turn_id_ontology BEFORE INSERT OR UPDATE ON public.qa_turn_event FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('turn_id', 'qa_turn');


--
-- Name: qa_turn trg_qa_turn_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_turn_release_id_ontology BEFORE INSERT OR UPDATE ON public.qa_turn FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: qa_turn trg_qa_turn_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_turn_scope_immutable BEFORE UPDATE ON public.qa_turn FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'qa_turn');


--
-- Name: qa_turn trg_qa_turn_session_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_qa_turn_session_id_ontology BEFORE INSERT OR UPDATE ON public.qa_turn FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('session_id', 'qa_session');


--
-- Name: quality_check trg_quality_check_object_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_quality_check_object_type_id_ontology BEFORE INSERT OR UPDATE ON public.quality_check FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('object_type_id', 'object_type');


--
-- Name: quality_check trg_quality_check_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_quality_check_scope_immutable BEFORE UPDATE ON public.quality_check FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'quality_check');


--
-- Name: quality_run trg_quality_run_check_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_quality_run_check_id_ontology BEFORE INSERT OR UPDATE ON public.quality_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('check_id', 'quality_check');


--
-- Name: quality_run trg_quality_run_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_quality_run_scope_immutable BEFORE UPDATE ON public.quality_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'quality_run');


--
-- Name: quality_schedule_cursor trg_quality_schedule_cursor_check_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_quality_schedule_cursor_check_id_ontology BEFORE INSERT OR UPDATE ON public.quality_schedule_cursor FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('check_id', 'quality_check');


--
-- Name: reconcile_task trg_reconcile_task_action_run_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_reconcile_task_action_run_id_ontology BEFORE INSERT OR UPDATE ON public.reconcile_task FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_run_id', 'action_run');


--
-- Name: reconcile_task trg_reconcile_task_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_reconcile_task_scope_immutable BEFORE UPDATE ON public.reconcile_task FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'reconcile_task');


--
-- Name: reconcile_task trg_reconcile_task_sys_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_reconcile_task_sys_id_ontology BEFORE INSERT OR UPDATE ON public.reconcile_task FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('sys_id', 'ext_system');


--
-- Name: rule trg_rule_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: rule_draft trg_rule_draft_rule_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_draft_rule_id_ontology BEFORE INSERT OR UPDATE ON public.rule_draft FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('rule_id', 'rule');


--
-- Name: rule_draft trg_rule_draft_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_draft_scope_immutable BEFORE UPDATE ON public.rule_draft FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'rule_draft');


--
-- Name: rule_hit trg_rule_hit_alert_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_hit_alert_id_ontology BEFORE INSERT OR UPDATE ON public.rule_hit FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('alert_id', 'alert');


--
-- Name: rule_hit trg_rule_hit_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_hit_append_only BEFORE DELETE OR UPDATE ON public.rule_hit FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: rule_hit trg_rule_hit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_hit_no_truncate BEFORE TRUNCATE ON public.rule_hit FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: rule_hit trg_rule_hit_rule_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_hit_rule_id_ontology BEFORE INSERT OR UPDATE ON public.rule_hit FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('rule_id', 'rule');


--
-- Name: rule_hit trg_rule_hit_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_hit_scope_immutable BEFORE UPDATE ON public.rule_hit FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'rule_hit');


--
-- Name: rule trg_rule_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_release_id_ontology BEFORE INSERT OR UPDATE ON public.rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: rule_schedule_cursor trg_rule_schedule_cursor_rule_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_schedule_cursor_rule_id_ontology BEFORE INSERT OR UPDATE ON public.rule_schedule_cursor FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('rule_id', 'rule');


--
-- Name: rule_schedule_cursor trg_rule_schedule_cursor_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_schedule_cursor_scope_immutable BEFORE UPDATE ON public.rule_schedule_cursor FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'rule_schedule_cursor');


--
-- Name: rule_schedule_run trg_rule_schedule_run_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_schedule_run_release_id_ontology BEFORE INSERT OR UPDATE ON public.rule_schedule_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('release_id', 'ontology_release');


--
-- Name: rule_schedule_run trg_rule_schedule_run_rule_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_schedule_run_rule_id_ontology BEFORE INSERT OR UPDATE ON public.rule_schedule_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('rule_id', 'rule');


--
-- Name: rule_schedule_run trg_rule_schedule_run_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_schedule_run_scope_immutable BEFORE UPDATE ON public.rule_schedule_run FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'rule_schedule_run');


--
-- Name: rule trg_rule_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_scope_immutable BEFORE UPDATE ON public.rule FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'rule');


--
-- Name: rule_version trg_rule_version_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_version_append_only BEFORE DELETE OR UPDATE ON public.rule_version FOR EACH ROW EXECUTE FUNCTION public.append_only_guard();


--
-- Name: rule_version trg_rule_version_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_version_no_truncate BEFORE TRUNCATE ON public.rule_version FOR EACH STATEMENT EXECUTE FUNCTION public.append_only_guard();


--
-- Name: rule_version trg_rule_version_rule_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_version_rule_id_ontology BEFORE INSERT OR UPDATE ON public.rule_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('rule_id', 'rule');


--
-- Name: rule_version trg_rule_version_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rule_version_scope_immutable BEFORE UPDATE ON public.rule_version FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'rule_version');


--
-- Name: scenario trg_scenario_base_release_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_base_release_id_ontology BEFORE INSERT OR UPDATE ON public.scenario FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('base_release_id', 'ontology_release');


--
-- Name: scenario_impact trg_scenario_impact_scenario_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_impact_scenario_id_ontology BEFORE INSERT OR UPDATE ON public.scenario_impact FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('scenario_id', 'scenario');


--
-- Name: scenario_impact trg_scenario_impact_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_impact_scope_immutable BEFORE UPDATE ON public.scenario_impact FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'scenario_impact');


--
-- Name: scenario_result trg_scenario_result_scenario_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_result_scenario_id_ontology BEFORE INSERT OR UPDATE ON public.scenario_result FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('scenario_id', 'scenario');


--
-- Name: scenario_result trg_scenario_result_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_result_scope_immutable BEFORE UPDATE ON public.scenario_result FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'scenario_result');


--
-- Name: scenario trg_scenario_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_scope_immutable BEFORE UPDATE ON public.scenario FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'scenario');


--
-- Name: scenario_template trg_scenario_template_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scenario_template_scope_immutable BEFORE UPDATE ON public.scenario_template FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'scenario_template');


--
-- Name: side_effect trg_side_effect_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_side_effect_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.side_effect FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: side_effect trg_side_effect_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_side_effect_scope_immutable BEFORE UPDATE ON public.side_effect FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'side_effect');


--
-- Name: store trg_store_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_store_scope_immutable BEFORE UPDATE ON public.store FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'store');


--
-- Name: submission_criterion trg_submission_criterion_action_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_submission_criterion_action_type_id_ontology BEFORE INSERT OR UPDATE ON public.submission_criterion FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_type_id', 'action_type');


--
-- Name: submission_criterion trg_submission_criterion_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_submission_criterion_scope_immutable BEFORE UPDATE ON public.submission_criterion FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'submission_criterion');


--
-- Name: sys_role_scope trg_sys_role_scope_object_type_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sys_role_scope_object_type_id_ontology BEFORE INSERT OR UPDATE ON public.sys_role_scope FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('object_type_id', 'object_type');


--
-- Name: sys_role_scope trg_sys_role_scope_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sys_role_scope_scope_immutable BEFORE UPDATE ON public.sys_role_scope FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'sys_role_scope');


--
-- Name: writeback_call trg_writeback_call_action_run_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_writeback_call_action_run_id_ontology BEFORE INSERT OR UPDATE ON public.writeback_call FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('action_run_id', 'action_run');


--
-- Name: writeback_call trg_writeback_call_scope_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_writeback_call_scope_immutable BEFORE UPDATE ON public.writeback_call FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('id', 'writeback_call');


--
-- Name: writeback_call trg_writeback_call_sys_id_ontology; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_writeback_call_sys_id_ontology BEFORE INSERT OR UPDATE ON public.writeback_call FOR EACH ROW EXECUTE FUNCTION public.ontology_reference_guard('sys_id', 'ext_system');


--
-- Name: action_param fk_action_param_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_param
    ADD CONSTRAINT fk_action_param_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id) ON DELETE CASCADE;


--
-- Name: action_param fk_action_param_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_param
    ADD CONSTRAINT fk_action_param_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_run fk_action_run_alert_id_alert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT fk_action_run_alert_id_alert FOREIGN KEY (alert_id) REFERENCES public.alert(id);


--
-- Name: action_run_daily_counter fk_action_run_daily_counter_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_counter
    ADD CONSTRAINT fk_action_run_daily_counter_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_run_daily_stat fk_action_run_daily_stat_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_stat
    ADD CONSTRAINT fk_action_run_daily_stat_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id) ON DELETE CASCADE;


--
-- Name: action_run_daily_stat fk_action_run_daily_stat_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_daily_stat
    ADD CONSTRAINT fk_action_run_daily_stat_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_run_log fk_action_run_log_action_run_id_action_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_log
    ADD CONSTRAINT fk_action_run_log_action_run_id_action_run FOREIGN KEY (action_run_id) REFERENCES public.action_run(id);


--
-- Name: action_run_log fk_action_run_log_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run_log
    ADD CONSTRAINT fk_action_run_log_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_run fk_action_run_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT fk_action_run_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_run fk_action_run_retried_as_id_action_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT fk_action_run_retried_as_id_action_run FOREIGN KEY (retried_as_id) REFERENCES public.action_run(id);


--
-- Name: action_run fk_action_run_retry_of_id_action_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_run
    ADD CONSTRAINT fk_action_run_retry_of_id_action_run FOREIGN KEY (retry_of_id) REFERENCES public.action_run(id);


--
-- Name: action_type fk_action_type_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type
    ADD CONSTRAINT fk_action_type_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_type fk_action_type_retired_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type
    ADD CONSTRAINT fk_action_type_retired_release_id_ontology_release FOREIGN KEY (retired_release_id) REFERENCES public.ontology_release(id);


--
-- Name: action_type_rule fk_action_type_rule_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_rule
    ADD CONSTRAINT fk_action_type_rule_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id) ON DELETE CASCADE;


--
-- Name: action_type_rule fk_action_type_rule_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_rule
    ADD CONSTRAINT fk_action_type_rule_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: action_type fk_action_type_sys_id_ext_system; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type
    ADD CONSTRAINT fk_action_type_sys_id_ext_system FOREIGN KEY (sys_id) REFERENCES public.ext_system(id);


--
-- Name: action_type_version fk_action_type_version_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_version
    ADD CONSTRAINT fk_action_type_version_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id);


--
-- Name: action_type_version fk_action_type_version_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_type_version
    ADD CONSTRAINT fk_action_type_version_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: alert fk_alert_action_run_id_action_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert
    ADD CONSTRAINT fk_alert_action_run_id_action_run FOREIGN KEY (action_run_id) REFERENCES public.action_run(id);


--
-- Name: alert_log fk_alert_log_alert_id_alert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_log
    ADD CONSTRAINT fk_alert_log_alert_id_alert FOREIGN KEY (alert_id) REFERENCES public.alert(id);


--
-- Name: alert_log fk_alert_log_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_log
    ADD CONSTRAINT fk_alert_log_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: alert fk_alert_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert
    ADD CONSTRAINT fk_alert_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: api_key_call fk_api_key_call_key_id_api_key; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_call
    ADD CONSTRAINT fk_api_key_call_key_id_api_key FOREIGN KEY (key_id) REFERENCES public.api_key(id);


--
-- Name: api_key fk_api_key_service_account_id_service_account; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT fk_api_key_service_account_id_service_account FOREIGN KEY (service_account_id) REFERENCES public.service_account(id);


--
-- Name: api_key_usage fk_api_key_usage_key_id_api_key; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_usage
    ADD CONSTRAINT fk_api_key_usage_key_id_api_key FOREIGN KEY (key_id) REFERENCES public.api_key(id) ON DELETE CASCADE;


--
-- Name: api_key fk_api_key_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT fk_api_key_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id);


--
-- Name: audit_export_job fk_audit_export_job_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_job
    ADD CONSTRAINT fk_audit_export_job_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id);


--
-- Name: audit_export_row fk_audit_export_row_audit_id_audit_log; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_row
    ADD CONSTRAINT fk_audit_export_row_audit_id_audit_log FOREIGN KEY (audit_id) REFERENCES public.audit_log(id);


--
-- Name: audit_export_row fk_audit_export_row_job_id_audit_export_job; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_export_row
    ADD CONSTRAINT fk_audit_export_row_job_id_audit_export_job FOREIGN KEY (job_id) REFERENCES public.audit_export_job(id);


--
-- Name: audit_log fk_audit_log_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT fk_audit_log_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: biz_order fk_biz_order_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_order
    ADD CONSTRAINT fk_biz_order_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: biz_order fk_biz_order_store_id_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_order
    ADD CONSTRAINT fk_biz_order_store_id_store FOREIGN KEY (store_id) REFERENCES public.store(id);


--
-- Name: change_request fk_change_request_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_request
    ADD CONSTRAINT fk_change_request_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: change_request fk_change_request_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_request
    ADD CONSTRAINT fk_change_request_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: cr_check fk_cr_check_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_check
    ADD CONSTRAINT fk_cr_check_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: cr_check fk_cr_check_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_check
    ADD CONSTRAINT fk_cr_check_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: cr_event fk_cr_event_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_event
    ADD CONSTRAINT fk_cr_event_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: cr_event fk_cr_event_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_event
    ADD CONSTRAINT fk_cr_event_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: cr_impact fk_cr_impact_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_impact
    ADD CONSTRAINT fk_cr_impact_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: cr_impact fk_cr_impact_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_impact
    ADD CONSTRAINT fk_cr_impact_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: cr_item fk_cr_item_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_item
    ADD CONSTRAINT fk_cr_item_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: cr_item fk_cr_item_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_item
    ADD CONSTRAINT fk_cr_item_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: cr_target fk_cr_target_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_target
    ADD CONSTRAINT fk_cr_target_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: cr_target fk_cr_target_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_target
    ADD CONSTRAINT fk_cr_target_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: cr_trial fk_cr_trial_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_trial
    ADD CONSTRAINT fk_cr_trial_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: cr_trial fk_cr_trial_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cr_trial
    ADD CONSTRAINT fk_cr_trial_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: datasource fk_datasource_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource
    ADD CONSTRAINT fk_datasource_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: datasource_run fk_datasource_run_datasource_id_datasource; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_run
    ADD CONSTRAINT fk_datasource_run_datasource_id_datasource FOREIGN KEY (datasource_id) REFERENCES public.datasource(id) ON DELETE CASCADE;


--
-- Name: datasource_run fk_datasource_run_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_run
    ADD CONSTRAINT fk_datasource_run_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: datasource_table fk_datasource_table_datasource_id_datasource; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_table
    ADD CONSTRAINT fk_datasource_table_datasource_id_datasource FOREIGN KEY (datasource_id) REFERENCES public.datasource(id) ON DELETE CASCADE;


--
-- Name: datasource_table fk_datasource_table_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasource_table
    ADD CONSTRAINT fk_datasource_table_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: decision_log fk_decision_log_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_log
    ADD CONSTRAINT fk_decision_log_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: dim_level fk_dim_level_dimension_id_dimension; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_level
    ADD CONSTRAINT fk_dim_level_dimension_id_dimension FOREIGN KEY (dimension_id) REFERENCES public.dimension(id) ON DELETE CASCADE;


--
-- Name: dim_level fk_dim_level_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_level
    ADD CONSTRAINT fk_dim_level_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: dim_mapping fk_dim_mapping_dimension_id_dimension; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_mapping
    ADD CONSTRAINT fk_dim_mapping_dimension_id_dimension FOREIGN KEY (dimension_id) REFERENCES public.dimension(id) ON DELETE CASCADE;


--
-- Name: dim_mapping fk_dim_mapping_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_mapping
    ADD CONSTRAINT fk_dim_mapping_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: dim_value fk_dim_value_dimension_id_dimension; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_value
    ADD CONSTRAINT fk_dim_value_dimension_id_dimension FOREIGN KEY (dimension_id) REFERENCES public.dimension(id) ON DELETE CASCADE;


--
-- Name: dim_value fk_dim_value_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dim_value
    ADD CONSTRAINT fk_dim_value_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: dimension fk_dimension_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dimension
    ADD CONSTRAINT fk_dimension_object_id_object_type FOREIGN KEY (object_id) REFERENCES public.object_type(id);


--
-- Name: dimension fk_dimension_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dimension
    ADD CONSTRAINT fk_dimension_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_daily_stat fk_event_daily_stat_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_daily_stat
    ADD CONSTRAINT fk_event_daily_stat_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id) ON DELETE CASCADE;


--
-- Name: event_daily_stat fk_event_daily_stat_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_daily_stat
    ADD CONSTRAINT fk_event_daily_stat_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_dedup fk_event_dedup_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_dedup
    ADD CONSTRAINT fk_event_dedup_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id) ON DELETE CASCADE;


--
-- Name: event_dedup fk_event_dedup_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_dedup
    ADD CONSTRAINT fk_event_dedup_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_delivery fk_event_delivery_event_id_event; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery
    ADD CONSTRAINT fk_event_delivery_event_id_event FOREIGN KEY (event_id) REFERENCES public.event(id);


--
-- Name: event_delivery fk_event_delivery_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery
    ADD CONSTRAINT fk_event_delivery_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_delivery_record fk_event_delivery_record_delivery_id_event_delivery; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery_record
    ADD CONSTRAINT fk_event_delivery_record_delivery_id_event_delivery FOREIGN KEY (delivery_id) REFERENCES public.event_delivery(id);


--
-- Name: event_delivery_record fk_event_delivery_record_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_delivery_record
    ADD CONSTRAINT fk_event_delivery_record_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_envelope_field fk_event_envelope_field_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_envelope_field
    ADD CONSTRAINT fk_event_envelope_field_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event fk_event_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT fk_event_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id) ON DELETE CASCADE;


--
-- Name: event fk_event_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT fk_event_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_payload_field fk_event_payload_field_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_payload_field
    ADD CONSTRAINT fk_event_payload_field_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id) ON DELETE CASCADE;


--
-- Name: event_payload_field fk_event_payload_field_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_payload_field
    ADD CONSTRAINT fk_event_payload_field_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event fk_event_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT fk_event_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: event_subscription fk_event_subscription_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_subscription
    ADD CONSTRAINT fk_event_subscription_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id) ON DELETE CASCADE;


--
-- Name: event_subscription fk_event_subscription_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_subscription
    ADD CONSTRAINT fk_event_subscription_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_type fk_event_type_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type
    ADD CONSTRAINT fk_event_type_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: event_type fk_event_type_retired_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type
    ADD CONSTRAINT fk_event_type_retired_release_id_ontology_release FOREIGN KEY (retired_release_id) REFERENCES public.ontology_release(id);


--
-- Name: event_type_version fk_event_type_version_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type_version
    ADD CONSTRAINT fk_event_type_version_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id);


--
-- Name: event_type_version fk_event_type_version_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_type_version
    ADD CONSTRAINT fk_event_type_version_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: ext_system fk_ext_system_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_system
    ADD CONSTRAINT fk_ext_system_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: fn_param fk_fn_param_function_id_function; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_param
    ADD CONSTRAINT fk_fn_param_function_id_function FOREIGN KEY (function_id) REFERENCES public.function(id);


--
-- Name: fn_param fk_fn_param_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_param
    ADD CONSTRAINT fk_fn_param_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: fn_use fk_fn_use_function_id_function; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_use
    ADD CONSTRAINT fk_fn_use_function_id_function FOREIGN KEY (function_id) REFERENCES public.function(id);


--
-- Name: fn_use fk_fn_use_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_use
    ADD CONSTRAINT fk_fn_use_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: fn_version fk_fn_version_author_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_version
    ADD CONSTRAINT fk_fn_version_author_id_sys_user FOREIGN KEY (author_id) REFERENCES public.sys_user(id);


--
-- Name: fn_version fk_fn_version_function_id_function; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_version
    ADD CONSTRAINT fk_fn_version_function_id_function FOREIGN KEY (function_id) REFERENCES public.function(id);


--
-- Name: fn_version fk_fn_version_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fn_version
    ADD CONSTRAINT fk_fn_version_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: function fk_function_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.function
    ADD CONSTRAINT fk_function_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: function fk_function_owner_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.function
    ADD CONSTRAINT fk_function_owner_id_sys_user FOREIGN KEY (owner_id) REFERENCES public.sys_user(id);


--
-- Name: governed_binding fk_governed_binding_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governed_binding
    ADD CONSTRAINT fk_governed_binding_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: knowledge_apply fk_knowledge_apply_knowledge_id_knowledge; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_apply
    ADD CONSTRAINT fk_knowledge_apply_knowledge_id_knowledge FOREIGN KEY (knowledge_id) REFERENCES public.knowledge(id) ON DELETE CASCADE;


--
-- Name: knowledge_apply fk_knowledge_apply_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_apply
    ADD CONSTRAINT fk_knowledge_apply_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: knowledge fk_knowledge_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge
    ADD CONSTRAINT fk_knowledge_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle_change fk_lifecycle_change_alert_id_alert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_change
    ADD CONSTRAINT fk_lifecycle_change_alert_id_alert FOREIGN KEY (alert_id) REFERENCES public.alert(id);


--
-- Name: lifecycle_change fk_lifecycle_change_lifecycle_id_lifecycle; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_change
    ADD CONSTRAINT fk_lifecycle_change_lifecycle_id_lifecycle FOREIGN KEY (lifecycle_id) REFERENCES public.lifecycle(id);


--
-- Name: lifecycle_change fk_lifecycle_change_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_change
    ADD CONSTRAINT fk_lifecycle_change_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle_change fk_lifecycle_change_transition_id_lifecycle_transition; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_change
    ADD CONSTRAINT fk_lifecycle_change_transition_id_lifecycle_transition FOREIGN KEY (transition_id) REFERENCES public.lifecycle_transition(id);


--
-- Name: lifecycle_draft fk_lifecycle_draft_lifecycle_id_lifecycle; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_draft
    ADD CONSTRAINT fk_lifecycle_draft_lifecycle_id_lifecycle FOREIGN KEY (lifecycle_id) REFERENCES public.lifecycle(id);


--
-- Name: lifecycle_draft fk_lifecycle_draft_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_draft
    ADD CONSTRAINT fk_lifecycle_draft_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle fk_lifecycle_object_type_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle
    ADD CONSTRAINT fk_lifecycle_object_type_id_object_type FOREIGN KEY (object_type_id) REFERENCES public.object_type(id);


--
-- Name: lifecycle fk_lifecycle_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle
    ADD CONSTRAINT fk_lifecycle_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle_state fk_lifecycle_state_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_state
    ADD CONSTRAINT fk_lifecycle_state_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle_state fk_lifecycle_state_version_id_lifecycle_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_state
    ADD CONSTRAINT fk_lifecycle_state_version_id_lifecycle_version FOREIGN KEY (version_id) REFERENCES public.lifecycle_version(id);


--
-- Name: lifecycle_transition fk_lifecycle_transition_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT fk_lifecycle_transition_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id);


--
-- Name: lifecycle_transition fk_lifecycle_transition_event_type_id_event_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT fk_lifecycle_transition_event_type_id_event_type FOREIGN KEY (event_type_id) REFERENCES public.event_type(id);


--
-- Name: lifecycle_transition fk_lifecycle_transition_from_state_id_lifecycle_state; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT fk_lifecycle_transition_from_state_id_lifecycle_state FOREIGN KEY (from_state_id) REFERENCES public.lifecycle_state(id);


--
-- Name: lifecycle_transition fk_lifecycle_transition_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT fk_lifecycle_transition_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle_transition fk_lifecycle_transition_to_state_id_lifecycle_state; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT fk_lifecycle_transition_to_state_id_lifecycle_state FOREIGN KEY (to_state_id) REFERENCES public.lifecycle_state(id);


--
-- Name: lifecycle_transition fk_lifecycle_transition_version_id_lifecycle_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_transition
    ADD CONSTRAINT fk_lifecycle_transition_version_id_lifecycle_version FOREIGN KEY (version_id) REFERENCES public.lifecycle_version(id);


--
-- Name: lifecycle_version fk_lifecycle_version_lifecycle_id_lifecycle; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_version
    ADD CONSTRAINT fk_lifecycle_version_lifecycle_id_lifecycle FOREIGN KEY (lifecycle_id) REFERENCES public.lifecycle(id);


--
-- Name: lifecycle_version fk_lifecycle_version_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_version
    ADD CONSTRAINT fk_lifecycle_version_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: lifecycle_version fk_lifecycle_version_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lifecycle_version
    ADD CONSTRAINT fk_lifecycle_version_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: link_quality fk_link_quality_link_type_id_link_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_quality
    ADD CONSTRAINT fk_link_quality_link_type_id_link_type FOREIGN KEY (link_type_id) REFERENCES public.link_type(id) ON DELETE CASCADE;


--
-- Name: link_quality fk_link_quality_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_quality
    ADD CONSTRAINT fk_link_quality_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: link_type fk_link_type_from_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_type
    ADD CONSTRAINT fk_link_type_from_object_id_object_type FOREIGN KEY (from_object_id) REFERENCES public.object_type(id);


--
-- Name: link_type fk_link_type_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_type
    ADD CONSTRAINT fk_link_type_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: link_type fk_link_type_to_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_type
    ADD CONSTRAINT fk_link_type_to_object_id_object_type FOREIGN KEY (to_object_id) REFERENCES public.object_type(id);


--
-- Name: metric fk_metric_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric
    ADD CONSTRAINT fk_metric_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: notification fk_notification_cr_id_change_request; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT fk_notification_cr_id_change_request FOREIGN KEY (cr_id) REFERENCES public.change_request(id) ON DELETE CASCADE;


--
-- Name: notification fk_notification_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT fk_notification_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: notification fk_notification_recipient_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT fk_notification_recipient_id_sys_user FOREIGN KEY (recipient_id) REFERENCES public.sys_user(id);


--
-- Name: notification fk_notification_source_event_id_cr_event; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT fk_notification_source_event_id_cr_event FOREIGN KEY (source_event_id) REFERENCES public.cr_event(id) ON DELETE CASCADE;


--
-- Name: notification fk_notification_source_outbox_id_outbox_event; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification
    ADD CONSTRAINT fk_notification_source_outbox_id_outbox_event FOREIGN KEY (source_outbox_id) REFERENCES public.outbox_event(id) ON DELETE CASCADE;


--
-- Name: object_type fk_object_type_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_type
    ADD CONSTRAINT fk_object_type_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: object_view fk_object_view_object_type_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_view
    ADD CONSTRAINT fk_object_view_object_type_id_object_type FOREIGN KEY (object_type_id) REFERENCES public.object_type(id) ON DELETE CASCADE;


--
-- Name: object_view fk_object_view_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.object_view
    ADD CONSTRAINT fk_object_view_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: onto_qa_path_example fk_onto_qa_path_example_from_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_example
    ADD CONSTRAINT fk_onto_qa_path_example_from_object_id_object_type FOREIGN KEY (from_object_id) REFERENCES public.object_type(id);


--
-- Name: onto_qa_path_example fk_onto_qa_path_example_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_example
    ADD CONSTRAINT fk_onto_qa_path_example_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: onto_qa_path_example fk_onto_qa_path_example_path_id_onto_qa_path; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_example
    ADD CONSTRAINT fk_onto_qa_path_example_path_id_onto_qa_path FOREIGN KEY (path_id) REFERENCES public.onto_qa_path(id) ON DELETE CASCADE;


--
-- Name: onto_qa_path_example fk_onto_qa_path_example_to_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_example
    ADD CONSTRAINT fk_onto_qa_path_example_to_object_id_object_type FOREIGN KEY (to_object_id) REFERENCES public.object_type(id);


--
-- Name: onto_qa_path fk_onto_qa_path_from_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path
    ADD CONSTRAINT fk_onto_qa_path_from_object_id_object_type FOREIGN KEY (from_object_id) REFERENCES public.object_type(id);


--
-- Name: onto_qa_path fk_onto_qa_path_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path
    ADD CONSTRAINT fk_onto_qa_path_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: onto_qa_path_step fk_onto_qa_path_step_link_type_id_link_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_step
    ADD CONSTRAINT fk_onto_qa_path_step_link_type_id_link_type FOREIGN KEY (link_type_id) REFERENCES public.link_type(id);


--
-- Name: onto_qa_path_step fk_onto_qa_path_step_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_step
    ADD CONSTRAINT fk_onto_qa_path_step_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: onto_qa_path_step fk_onto_qa_path_step_path_id_onto_qa_path; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path_step
    ADD CONSTRAINT fk_onto_qa_path_step_path_id_onto_qa_path FOREIGN KEY (path_id) REFERENCES public.onto_qa_path(id) ON DELETE CASCADE;


--
-- Name: onto_qa_path fk_onto_qa_path_to_object_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.onto_qa_path
    ADD CONSTRAINT fk_onto_qa_path_to_object_id_object_type FOREIGN KEY (to_object_id) REFERENCES public.object_type(id);


--
-- Name: ontology_edit_rule fk_ontology_edit_rule_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_edit_rule
    ADD CONSTRAINT fk_ontology_edit_rule_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id) ON DELETE CASCADE;


--
-- Name: ontology_edit_rule fk_ontology_edit_rule_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_edit_rule
    ADD CONSTRAINT fk_ontology_edit_rule_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: ontology_member fk_ontology_member_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_member
    ADD CONSTRAINT fk_ontology_member_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: ontology_member fk_ontology_member_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_member
    ADD CONSTRAINT fk_ontology_member_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id);


--
-- Name: ontology_release fk_ontology_release_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_release
    ADD CONSTRAINT fk_ontology_release_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: ontology_release fk_ontology_release_parent_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_release
    ADD CONSTRAINT fk_ontology_release_parent_id_ontology_release FOREIGN KEY (parent_id) REFERENCES public.ontology_release(id);


--
-- Name: ontology_release fk_ontology_release_published_by_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_release
    ADD CONSTRAINT fk_ontology_release_published_by_id_sys_user FOREIGN KEY (published_by_id) REFERENCES public.sys_user(id);


--
-- Name: ontology_release fk_ontology_release_rollback_of_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_release
    ADD CONSTRAINT fk_ontology_release_rollback_of_id_ontology_release FOREIGN KEY (rollback_of_id) REFERENCES public.ontology_release(id);


--
-- Name: ontology_role_binding fk_ontology_role_binding_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_role_binding
    ADD CONSTRAINT fk_ontology_role_binding_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: ontology_role_binding fk_ontology_role_binding_role_id_sys_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ontology_role_binding
    ADD CONSTRAINT fk_ontology_role_binding_role_id_sys_role FOREIGN KEY (role_id) REFERENCES public.sys_role(id) ON DELETE CASCADE;


--
-- Name: order_line fk_order_line_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_line
    ADD CONSTRAINT fk_order_line_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: order_line fk_order_line_order_id_biz_order; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_line
    ADD CONSTRAINT fk_order_line_order_id_biz_order FOREIGN KEY (order_id) REFERENCES public.biz_order(id);


--
-- Name: order_line fk_order_line_product_id_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_line
    ADD CONSTRAINT fk_order_line_product_id_product FOREIGN KEY (product_id) REFERENCES public.product(id);


--
-- Name: outbox_event fk_outbox_event_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_event
    ADD CONSTRAINT fk_outbox_event_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: product fk_product_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product
    ADD CONSTRAINT fk_product_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: property fk_property_object_type_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.property
    ADD CONSTRAINT fk_property_object_type_id_object_type FOREIGN KEY (object_type_id) REFERENCES public.object_type(id) ON DELETE CASCADE;


--
-- Name: property fk_property_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.property
    ADD CONSTRAINT fk_property_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_cert fk_qa_cert_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert
    ADD CONSTRAINT fk_qa_cert_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_cert_variant fk_qa_cert_variant_cert_id_qa_cert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert_variant
    ADD CONSTRAINT fk_qa_cert_variant_cert_id_qa_cert FOREIGN KEY (cert_id) REFERENCES public.qa_cert(id) ON DELETE CASCADE;


--
-- Name: qa_cert_variant fk_qa_cert_variant_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_cert_variant
    ADD CONSTRAINT fk_qa_cert_variant_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_eval_fail fk_qa_eval_fail_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_fail
    ADD CONSTRAINT fk_qa_eval_fail_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_eval_fail fk_qa_eval_fail_question_id_qa_eval_question; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_fail
    ADD CONSTRAINT fk_qa_eval_fail_question_id_qa_eval_question FOREIGN KEY (question_id) REFERENCES public.qa_eval_question(id);


--
-- Name: qa_eval_fail fk_qa_eval_fail_run_id_qa_eval_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_fail
    ADD CONSTRAINT fk_qa_eval_fail_run_id_qa_eval_run FOREIGN KEY (run_id) REFERENCES public.qa_eval_run(id);


--
-- Name: qa_eval_question fk_qa_eval_question_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_question
    ADD CONSTRAINT fk_qa_eval_question_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_eval_run fk_qa_eval_run_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_run
    ADD CONSTRAINT fk_qa_eval_run_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_eval_run fk_qa_eval_run_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_eval_run
    ADD CONSTRAINT fk_qa_eval_run_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: qa_favorite fk_qa_favorite_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_favorite
    ADD CONSTRAINT fk_qa_favorite_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_favorite fk_qa_favorite_owner_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_favorite
    ADD CONSTRAINT fk_qa_favorite_owner_id_sys_user FOREIGN KEY (owner_id) REFERENCES public.sys_user(id);


--
-- Name: qa_feedback fk_qa_feedback_cert_id_qa_cert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT fk_qa_feedback_cert_id_qa_cert FOREIGN KEY (cert_id) REFERENCES public.qa_cert(id);


--
-- Name: qa_feedback fk_qa_feedback_cert_variant_id_qa_cert_variant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT fk_qa_feedback_cert_variant_id_qa_cert_variant FOREIGN KEY (cert_variant_id) REFERENCES public.qa_cert_variant(id) ON DELETE SET NULL;


--
-- Name: qa_feedback fk_qa_feedback_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT fk_qa_feedback_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_feedback fk_qa_feedback_turn_id_qa_turn; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT fk_qa_feedback_turn_id_qa_turn FOREIGN KEY (turn_id) REFERENCES public.qa_turn(id) ON DELETE SET NULL;


--
-- Name: qa_feedback fk_qa_feedback_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_feedback
    ADD CONSTRAINT fk_qa_feedback_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id);


--
-- Name: qa_freq_question fk_qa_freq_question_cert_id_qa_cert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_freq_question
    ADD CONSTRAINT fk_qa_freq_question_cert_id_qa_cert FOREIGN KEY (cert_id) REFERENCES public.qa_cert(id) ON DELETE SET NULL;


--
-- Name: qa_freq_question fk_qa_freq_question_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_freq_question
    ADD CONSTRAINT fk_qa_freq_question_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_object_type_view fk_qa_object_type_view_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_object_type_view
    ADD CONSTRAINT fk_qa_object_type_view_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_object_type_view fk_qa_object_type_view_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_object_type_view
    ADD CONSTRAINT fk_qa_object_type_view_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: qa_page_card fk_qa_page_card_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_card
    ADD CONSTRAINT fk_qa_page_card_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_page_card fk_qa_page_card_page_id_qa_page; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_card
    ADD CONSTRAINT fk_qa_page_card_page_id_qa_page FOREIGN KEY (page_id) REFERENCES public.qa_page(id) ON DELETE CASCADE;


--
-- Name: qa_page fk_qa_page_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page
    ADD CONSTRAINT fk_qa_page_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_page fk_qa_page_owner_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page
    ADD CONSTRAINT fk_qa_page_owner_id_sys_user FOREIGN KEY (owner_id) REFERENCES public.sys_user(id);


--
-- Name: qa_page_share_member fk_qa_page_share_member_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_share_member
    ADD CONSTRAINT fk_qa_page_share_member_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_page_share_member fk_qa_page_share_member_page_id_qa_page; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_share_member
    ADD CONSTRAINT fk_qa_page_share_member_page_id_qa_page FOREIGN KEY (page_id) REFERENCES public.qa_page(id) ON DELETE CASCADE;


--
-- Name: qa_page_share_member fk_qa_page_share_member_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_share_member
    ADD CONSTRAINT fk_qa_page_share_member_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id);


--
-- Name: qa_page_subscription fk_qa_page_subscription_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_subscription
    ADD CONSTRAINT fk_qa_page_subscription_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_page_subscription fk_qa_page_subscription_page_id_qa_page; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_page_subscription
    ADD CONSTRAINT fk_qa_page_subscription_page_id_qa_page FOREIGN KEY (page_id) REFERENCES public.qa_page(id) ON DELETE CASCADE;


--
-- Name: qa_permission_request fk_qa_permission_request_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_permission_request
    ADD CONSTRAINT fk_qa_permission_request_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_permission_request fk_qa_permission_request_requester_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_permission_request
    ADD CONSTRAINT fk_qa_permission_request_requester_id_sys_user FOREIGN KEY (requester_id) REFERENCES public.sys_user(id);


--
-- Name: qa_permission_request fk_qa_permission_request_source_turn_id_qa_turn; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_permission_request
    ADD CONSTRAINT fk_qa_permission_request_source_turn_id_qa_turn FOREIGN KEY (source_turn_id) REFERENCES public.qa_turn(id) ON DELETE SET NULL;


--
-- Name: qa_query_log fk_qa_query_log_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_query_log
    ADD CONSTRAINT fk_qa_query_log_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_query_log fk_qa_query_log_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_query_log
    ADD CONSTRAINT fk_qa_query_log_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id);


--
-- Name: qa_session fk_qa_session_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session
    ADD CONSTRAINT fk_qa_session_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_session_option fk_qa_session_option_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session_option
    ADD CONSTRAINT fk_qa_session_option_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_session fk_qa_session_owner_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_session
    ADD CONSTRAINT fk_qa_session_owner_id_sys_user FOREIGN KEY (owner_id) REFERENCES public.sys_user(id);


--
-- Name: qa_synonym_link fk_qa_synonym_link_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_synonym_link
    ADD CONSTRAINT fk_qa_synonym_link_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_template_card fk_qa_template_card_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template_card
    ADD CONSTRAINT fk_qa_template_card_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_template_card fk_qa_template_card_template_id_qa_template; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template_card
    ADD CONSTRAINT fk_qa_template_card_template_id_qa_template FOREIGN KEY (template_id) REFERENCES public.qa_template(id) ON DELETE CASCADE;


--
-- Name: qa_template fk_qa_template_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_template
    ADD CONSTRAINT fk_qa_template_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_turn_event fk_qa_turn_event_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn_event
    ADD CONSTRAINT fk_qa_turn_event_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_turn_event fk_qa_turn_event_turn_id_qa_turn; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn_event
    ADD CONSTRAINT fk_qa_turn_event_turn_id_qa_turn FOREIGN KEY (turn_id) REFERENCES public.qa_turn(id) ON DELETE CASCADE;


--
-- Name: qa_turn fk_qa_turn_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn
    ADD CONSTRAINT fk_qa_turn_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: qa_turn fk_qa_turn_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn
    ADD CONSTRAINT fk_qa_turn_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: qa_turn fk_qa_turn_session_id_qa_session; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.qa_turn
    ADD CONSTRAINT fk_qa_turn_session_id_qa_session FOREIGN KEY (session_id) REFERENCES public.qa_session(id) ON DELETE CASCADE;


--
-- Name: quality_check fk_quality_check_object_type_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_check
    ADD CONSTRAINT fk_quality_check_object_type_id_object_type FOREIGN KEY (object_type_id) REFERENCES public.object_type(id);


--
-- Name: quality_check fk_quality_check_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_check
    ADD CONSTRAINT fk_quality_check_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: quality_run fk_quality_run_check_id_quality_check; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_run
    ADD CONSTRAINT fk_quality_run_check_id_quality_check FOREIGN KEY (check_id) REFERENCES public.quality_check(id);


--
-- Name: quality_run fk_quality_run_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_run
    ADD CONSTRAINT fk_quality_run_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: quality_schedule_cursor fk_quality_schedule_cursor_check_id_quality_check; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_schedule_cursor
    ADD CONSTRAINT fk_quality_schedule_cursor_check_id_quality_check FOREIGN KEY (check_id) REFERENCES public.quality_check(id);


--
-- Name: quality_schedule_cursor fk_quality_schedule_cursor_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_schedule_cursor
    ADD CONSTRAINT fk_quality_schedule_cursor_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: reconcile_task fk_reconcile_task_action_run_id_action_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconcile_task
    ADD CONSTRAINT fk_reconcile_task_action_run_id_action_run FOREIGN KEY (action_run_id) REFERENCES public.action_run(id);


--
-- Name: reconcile_task fk_reconcile_task_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconcile_task
    ADD CONSTRAINT fk_reconcile_task_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: reconcile_task fk_reconcile_task_sys_id_ext_system; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconcile_task
    ADD CONSTRAINT fk_reconcile_task_sys_id_ext_system FOREIGN KEY (sys_id) REFERENCES public.ext_system(id);


--
-- Name: rule fk_rule_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT fk_rule_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id);


--
-- Name: rule_draft fk_rule_draft_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_draft
    ADD CONSTRAINT fk_rule_draft_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: rule_draft fk_rule_draft_rule_id_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_draft
    ADD CONSTRAINT fk_rule_draft_rule_id_rule FOREIGN KEY (rule_id) REFERENCES public.rule(id);


--
-- Name: rule_hit fk_rule_hit_alert_id_alert; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_hit
    ADD CONSTRAINT fk_rule_hit_alert_id_alert FOREIGN KEY (alert_id) REFERENCES public.alert(id);


--
-- Name: rule_hit fk_rule_hit_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_hit
    ADD CONSTRAINT fk_rule_hit_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: rule_hit fk_rule_hit_rule_id_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_hit
    ADD CONSTRAINT fk_rule_hit_rule_id_rule FOREIGN KEY (rule_id) REFERENCES public.rule(id);


--
-- Name: rule fk_rule_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT fk_rule_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: rule fk_rule_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT fk_rule_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: rule_schedule_cursor fk_rule_schedule_cursor_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_cursor
    ADD CONSTRAINT fk_rule_schedule_cursor_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: rule_schedule_cursor fk_rule_schedule_cursor_rule_id_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_cursor
    ADD CONSTRAINT fk_rule_schedule_cursor_rule_id_rule FOREIGN KEY (rule_id) REFERENCES public.rule(id);


--
-- Name: rule_schedule_run fk_rule_schedule_run_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_run
    ADD CONSTRAINT fk_rule_schedule_run_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: rule_schedule_run fk_rule_schedule_run_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_run
    ADD CONSTRAINT fk_rule_schedule_run_release_id_ontology_release FOREIGN KEY (release_id) REFERENCES public.ontology_release(id);


--
-- Name: rule_schedule_run fk_rule_schedule_run_rule_id_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_schedule_run
    ADD CONSTRAINT fk_rule_schedule_run_rule_id_rule FOREIGN KEY (rule_id) REFERENCES public.rule(id);


--
-- Name: rule_version fk_rule_version_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_version
    ADD CONSTRAINT fk_rule_version_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: rule_version fk_rule_version_rule_id_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_version
    ADD CONSTRAINT fk_rule_version_rule_id_rule FOREIGN KEY (rule_id) REFERENCES public.rule(id);


--
-- Name: scenario fk_scenario_base_release_id_ontology_release; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario
    ADD CONSTRAINT fk_scenario_base_release_id_ontology_release FOREIGN KEY (base_release_id) REFERENCES public.ontology_release(id);


--
-- Name: scenario_impact fk_scenario_impact_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_impact
    ADD CONSTRAINT fk_scenario_impact_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: scenario_impact fk_scenario_impact_scenario_id_scenario; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_impact
    ADD CONSTRAINT fk_scenario_impact_scenario_id_scenario FOREIGN KEY (scenario_id) REFERENCES public.scenario(id);


--
-- Name: scenario fk_scenario_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario
    ADD CONSTRAINT fk_scenario_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: scenario_result fk_scenario_result_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_result
    ADD CONSTRAINT fk_scenario_result_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: scenario_result fk_scenario_result_scenario_id_scenario; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_result
    ADD CONSTRAINT fk_scenario_result_scenario_id_scenario FOREIGN KEY (scenario_id) REFERENCES public.scenario(id);


--
-- Name: scenario_template fk_scenario_template_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scenario_template
    ADD CONSTRAINT fk_scenario_template_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: service_account fk_service_account_role_id_sys_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_account
    ADD CONSTRAINT fk_service_account_role_id_sys_role FOREIGN KEY (role_id) REFERENCES public.sys_role(id);


--
-- Name: side_effect fk_side_effect_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.side_effect
    ADD CONSTRAINT fk_side_effect_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id) ON DELETE CASCADE;


--
-- Name: side_effect fk_side_effect_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.side_effect
    ADD CONSTRAINT fk_side_effect_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: store fk_store_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store
    ADD CONSTRAINT fk_store_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: submission_criterion fk_submission_criterion_action_type_id_action_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_criterion
    ADD CONSTRAINT fk_submission_criterion_action_type_id_action_type FOREIGN KEY (action_type_id) REFERENCES public.action_type(id) ON DELETE CASCADE;


--
-- Name: submission_criterion fk_submission_criterion_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_criterion
    ADD CONSTRAINT fk_submission_criterion_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: sys_role_member fk_sys_role_member_role_id_sys_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_member
    ADD CONSTRAINT fk_sys_role_member_role_id_sys_role FOREIGN KEY (role_id) REFERENCES public.sys_role(id) ON DELETE CASCADE;


--
-- Name: sys_role_member fk_sys_role_member_user_id_sys_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_member
    ADD CONSTRAINT fk_sys_role_member_user_id_sys_user FOREIGN KEY (user_id) REFERENCES public.sys_user(id) ON DELETE CASCADE;


--
-- Name: sys_role_scope fk_sys_role_scope_object_type_id_object_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_scope
    ADD CONSTRAINT fk_sys_role_scope_object_type_id_object_type FOREIGN KEY (object_type_id) REFERENCES public.object_type(id) ON DELETE SET NULL;


--
-- Name: sys_role_scope fk_sys_role_scope_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_scope
    ADD CONSTRAINT fk_sys_role_scope_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: sys_role_scope fk_sys_role_scope_role_id_sys_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sys_role_scope
    ADD CONSTRAINT fk_sys_role_scope_role_id_sys_role FOREIGN KEY (role_id) REFERENCES public.sys_role(id) ON DELETE CASCADE;


--
-- Name: writeback_call fk_writeback_call_action_run_id_action_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.writeback_call
    ADD CONSTRAINT fk_writeback_call_action_run_id_action_run FOREIGN KEY (action_run_id) REFERENCES public.action_run(id);


--
-- Name: writeback_call fk_writeback_call_ontology_id_ontology_workspace; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.writeback_call
    ADD CONSTRAINT fk_writeback_call_ontology_id_ontology_workspace FOREIGN KEY (ontology_id) REFERENCES public.ontology_workspace(id);


--
-- Name: writeback_call fk_writeback_call_sys_id_ext_system; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.writeback_call
    ADD CONSTRAINT fk_writeback_call_sys_id_ext_system FOREIGN KEY (sys_id) REFERENCES public.ext_system(id);


--
-- Name: maintenance_task_demo_20261028 maintenance_task_demo_20261028_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_task_demo_20261028
    ADD CONSTRAINT maintenance_task_demo_20261028_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.maintenance_asset_demo_20261028(id);


--
-- Name: procrastinate_events procrastinate_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_events
    ADD CONSTRAINT procrastinate_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.procrastinate_jobs(id) ON DELETE CASCADE;


--
-- Name: procrastinate_jobs procrastinate_jobs_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_jobs
    ADD CONSTRAINT procrastinate_jobs_worker_id_fkey FOREIGN KEY (worker_id) REFERENCES public.procrastinate_workers(id) ON DELETE SET NULL;


--
-- Name: procrastinate_periodic_defers procrastinate_periodic_defers_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procrastinate_periodic_defers
    ADD CONSTRAINT procrastinate_periodic_defers_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.procrastinate_jobs(id);


--
-- Name: action_param; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_param ENABLE ROW LEVEL SECURITY;

--
-- Name: action_run; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_run ENABLE ROW LEVEL SECURITY;

--
-- Name: action_run_daily_counter; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_run_daily_counter ENABLE ROW LEVEL SECURITY;

--
-- Name: action_run_daily_stat; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_run_daily_stat ENABLE ROW LEVEL SECURITY;

--
-- Name: action_run_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_run_log ENABLE ROW LEVEL SECURITY;

--
-- Name: action_type; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_type ENABLE ROW LEVEL SECURITY;

--
-- Name: action_type_rule; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_type_rule ENABLE ROW LEVEL SECURITY;

--
-- Name: action_type_version; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_type_version ENABLE ROW LEVEL SECURITY;

--
-- Name: alert; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alert ENABLE ROW LEVEL SECURITY;

--
-- Name: alert_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alert_log ENABLE ROW LEVEL SECURITY;

--
-- Name: biz_order; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_order ENABLE ROW LEVEL SECURITY;

--
-- Name: change_request; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.change_request ENABLE ROW LEVEL SECURITY;

--
-- Name: cr_check; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cr_check ENABLE ROW LEVEL SECURITY;

--
-- Name: cr_event; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cr_event ENABLE ROW LEVEL SECURITY;

--
-- Name: cr_impact; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cr_impact ENABLE ROW LEVEL SECURITY;

--
-- Name: cr_item; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cr_item ENABLE ROW LEVEL SECURITY;

--
-- Name: cr_target; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cr_target ENABLE ROW LEVEL SECURITY;

--
-- Name: cr_trial; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cr_trial ENABLE ROW LEVEL SECURITY;

--
-- Name: datasource; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.datasource ENABLE ROW LEVEL SECURITY;

--
-- Name: datasource_run; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.datasource_run ENABLE ROW LEVEL SECURITY;

--
-- Name: datasource_table; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.datasource_table ENABLE ROW LEVEL SECURITY;

--
-- Name: decision_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.decision_log ENABLE ROW LEVEL SECURITY;

--
-- Name: dim_level; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dim_level ENABLE ROW LEVEL SECURITY;

--
-- Name: dim_mapping; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dim_mapping ENABLE ROW LEVEL SECURITY;

--
-- Name: dim_value; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dim_value ENABLE ROW LEVEL SECURITY;

--
-- Name: dimension; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dimension ENABLE ROW LEVEL SECURITY;

--
-- Name: event; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event ENABLE ROW LEVEL SECURITY;

--
-- Name: event_daily_stat; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_daily_stat ENABLE ROW LEVEL SECURITY;

--
-- Name: event_dedup; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_dedup ENABLE ROW LEVEL SECURITY;

--
-- Name: event_delivery; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_delivery ENABLE ROW LEVEL SECURITY;

--
-- Name: event_delivery_record; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_delivery_record ENABLE ROW LEVEL SECURITY;

--
-- Name: event_envelope_field; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_envelope_field ENABLE ROW LEVEL SECURITY;

--
-- Name: event_payload_field; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_payload_field ENABLE ROW LEVEL SECURITY;

--
-- Name: event_subscription; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_subscription ENABLE ROW LEVEL SECURITY;

--
-- Name: event_type; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_type ENABLE ROW LEVEL SECURITY;

--
-- Name: event_type_version; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.event_type_version ENABLE ROW LEVEL SECURITY;

--
-- Name: ext_system; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ext_system ENABLE ROW LEVEL SECURITY;

--
-- Name: fn_param; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fn_param ENABLE ROW LEVEL SECURITY;

--
-- Name: fn_use; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fn_use ENABLE ROW LEVEL SECURITY;

--
-- Name: fn_version; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fn_version ENABLE ROW LEVEL SECURITY;

--
-- Name: function; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.function ENABLE ROW LEVEL SECURITY;

--
-- Name: governed_binding; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.governed_binding ENABLE ROW LEVEL SECURITY;

--
-- Name: knowledge; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.knowledge ENABLE ROW LEVEL SECURITY;

--
-- Name: knowledge_apply; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.knowledge_apply ENABLE ROW LEVEL SECURITY;

--
-- Name: lifecycle; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle ENABLE ROW LEVEL SECURITY;

--
-- Name: lifecycle_change; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_change ENABLE ROW LEVEL SECURITY;

--
-- Name: lifecycle_draft; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_draft ENABLE ROW LEVEL SECURITY;

--
-- Name: lifecycle_state; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_state ENABLE ROW LEVEL SECURITY;

--
-- Name: lifecycle_transition; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_transition ENABLE ROW LEVEL SECURITY;

--
-- Name: lifecycle_version; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lifecycle_version ENABLE ROW LEVEL SECURITY;

--
-- Name: link_quality; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.link_quality ENABLE ROW LEVEL SECURITY;

--
-- Name: link_type; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.link_type ENABLE ROW LEVEL SECURITY;

--
-- Name: maintenance_asset_demo_20261028; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.maintenance_asset_demo_20261028 ENABLE ROW LEVEL SECURITY;

--
-- Name: maintenance_asset_extra_demo_20261028; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.maintenance_asset_extra_demo_20261028 ENABLE ROW LEVEL SECURITY;

--
-- Name: maintenance_asset_extra_demo_20261028 maintenance_extra_workspace; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY maintenance_extra_workspace ON public.maintenance_asset_extra_demo_20261028 USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: maintenance_quality_demo_20261028; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.maintenance_quality_demo_20261028 ENABLE ROW LEVEL SECURITY;

--
-- Name: maintenance_quality_demo_20261028 maintenance_quality_workspace; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY maintenance_quality_workspace ON public.maintenance_quality_demo_20261028 USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: maintenance_task_demo_20261028; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.maintenance_task_demo_20261028 ENABLE ROW LEVEL SECURITY;

--
-- Name: maintenance_asset_demo_20261028 maintenance_workspace; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY maintenance_workspace ON public.maintenance_asset_demo_20261028 USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: maintenance_task_demo_20261028 maintenance_workspace; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY maintenance_workspace ON public.maintenance_task_demo_20261028 USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: metric; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.metric ENABLE ROW LEVEL SECURITY;

--
-- Name: notification; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.notification ENABLE ROW LEVEL SECURITY;

--
-- Name: object_type; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.object_type ENABLE ROW LEVEL SECURITY;

--
-- Name: object_view; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.object_view ENABLE ROW LEVEL SECURITY;

--
-- Name: onto_qa_path; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.onto_qa_path ENABLE ROW LEVEL SECURITY;

--
-- Name: onto_qa_path_example; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.onto_qa_path_example ENABLE ROW LEVEL SECURITY;

--
-- Name: onto_qa_path_step; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.onto_qa_path_step ENABLE ROW LEVEL SECURITY;

--
-- Name: ontology_edit_rule; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ontology_edit_rule ENABLE ROW LEVEL SECURITY;

--
-- Name: ontology_release; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ontology_release ENABLE ROW LEVEL SECURITY;

--
-- Name: action_param ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_param USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_run ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_run USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_run_daily_counter ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_run_daily_counter USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_run_daily_stat ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_run_daily_stat USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_run_log ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_run_log USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_type ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_type USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_type_rule ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_type_rule USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: action_type_version ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.action_type_version USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: alert ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.alert USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: alert_log ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.alert_log USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: biz_order ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.biz_order USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: change_request ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.change_request USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: cr_check ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.cr_check USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: cr_event ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.cr_event USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: cr_impact ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.cr_impact USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: cr_item ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.cr_item USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: cr_target ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.cr_target USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: cr_trial ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.cr_trial USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: datasource ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.datasource USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: datasource_run ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.datasource_run USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: datasource_table ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.datasource_table USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: decision_log ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.decision_log USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: dim_level ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.dim_level USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: dim_mapping ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.dim_mapping USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: dim_value ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.dim_value USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: dimension ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.dimension USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_daily_stat ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_daily_stat USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_dedup ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_dedup USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_delivery ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_delivery USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_delivery_record ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_delivery_record USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_envelope_field ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_envelope_field USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_payload_field ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_payload_field USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_subscription ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_subscription USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_type ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_type USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: event_type_version ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.event_type_version USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: ext_system ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.ext_system USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: fn_param ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.fn_param USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: fn_use ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.fn_use USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: fn_version ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.fn_version USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: function ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.function USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: governed_binding ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.governed_binding USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: knowledge ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.knowledge USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: knowledge_apply ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.knowledge_apply USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: lifecycle ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.lifecycle USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: lifecycle_change ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.lifecycle_change USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: lifecycle_draft ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.lifecycle_draft USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: lifecycle_state ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.lifecycle_state USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: lifecycle_transition ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.lifecycle_transition USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: lifecycle_version ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.lifecycle_version USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: link_quality ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.link_quality USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: link_type ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.link_type USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: metric ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.metric USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: notification ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.notification USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: object_type ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.object_type USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: object_view ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.object_view USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: onto_qa_path ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.onto_qa_path USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: onto_qa_path_example ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.onto_qa_path_example USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: onto_qa_path_step ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.onto_qa_path_step USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: ontology_edit_rule ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.ontology_edit_rule USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: ontology_release ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.ontology_release USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: order_line ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.order_line USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: outbox_event ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.outbox_event USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: product ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.product USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: property ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.property USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_cert ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_cert USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_cert_variant ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_cert_variant USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_eval_fail ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_eval_fail USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_eval_question ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_eval_question USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_eval_run ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_eval_run USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_favorite ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_favorite USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_feedback ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_feedback USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_freq_question ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_freq_question USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_object_type_view ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_object_type_view USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_page ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_page USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_page_card ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_page_card USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_page_share_member ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_page_share_member USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_page_subscription ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_page_subscription USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_permission_request ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_permission_request USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_query_log ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_query_log USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_session ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_session USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_session_option ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_session_option USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_synonym_link ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_synonym_link USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_template ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_template USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_template_card ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_template_card USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_turn ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_turn USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: qa_turn_event ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.qa_turn_event USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: quality_check ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.quality_check USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: quality_run ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.quality_run USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: quality_schedule_cursor ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.quality_schedule_cursor USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: reconcile_task ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.reconcile_task USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: rule ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.rule USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: rule_draft ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.rule_draft USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: rule_hit ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.rule_hit USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: rule_schedule_cursor ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.rule_schedule_cursor USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: rule_schedule_run ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.rule_schedule_run USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: rule_version ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.rule_version USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: scenario ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.scenario USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: scenario_impact ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.scenario_impact USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: scenario_result ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.scenario_result USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: scenario_template ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.scenario_template USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: side_effect ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.side_effect USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: store ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.store USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: submission_criterion ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.submission_criterion USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: sys_role_scope ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.sys_role_scope USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: writeback_call ontology_scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ontology_scope ON public.writeback_call USING ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)) WITH CHECK ((ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint));


--
-- Name: order_line; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.order_line ENABLE ROW LEVEL SECURITY;

--
-- Name: outbox_event; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.outbox_event ENABLE ROW LEVEL SECURITY;

--
-- Name: product; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.product ENABLE ROW LEVEL SECURITY;

--
-- Name: property; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.property ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_cert; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_cert ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_cert_variant; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_cert_variant ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_eval_fail; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_eval_fail ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_eval_question; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_eval_question ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_eval_run; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_eval_run ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_favorite; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_favorite ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_feedback; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_feedback ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_freq_question; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_freq_question ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_object_type_view; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_object_type_view ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_page; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_page ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_page_card; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_page_card ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_page_share_member; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_page_share_member ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_page_subscription; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_page_subscription ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_permission_request; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_permission_request ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_query_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_query_log ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_session; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_session ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_session_option; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_session_option ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_synonym_link; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_synonym_link ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_template; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_template ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_template_card; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_template_card ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_turn; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_turn ENABLE ROW LEVEL SECURITY;

--
-- Name: qa_turn_event; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.qa_turn_event ENABLE ROW LEVEL SECURITY;

--
-- Name: quality_check; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.quality_check ENABLE ROW LEVEL SECURITY;

--
-- Name: quality_run; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.quality_run ENABLE ROW LEVEL SECURITY;

--
-- Name: quality_schedule_cursor; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.quality_schedule_cursor ENABLE ROW LEVEL SECURITY;

--
-- Name: reconcile_task; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reconcile_task ENABLE ROW LEVEL SECURITY;

--
-- Name: rescue_incident_w4_demo; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rescue_incident_w4_demo ENABLE ROW LEVEL SECURITY;

--
-- Name: rescue_team_w4_demo; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rescue_team_w4_demo ENABLE ROW LEVEL SECURITY;

--
-- Name: rule; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rule ENABLE ROW LEVEL SECURITY;

--
-- Name: rule_draft; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rule_draft ENABLE ROW LEVEL SECURITY;

--
-- Name: rule_hit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rule_hit ENABLE ROW LEVEL SECURITY;

--
-- Name: rule_schedule_cursor; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rule_schedule_cursor ENABLE ROW LEVEL SECURITY;

--
-- Name: rule_schedule_run; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rule_schedule_run ENABLE ROW LEVEL SECURITY;

--
-- Name: rule_version; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rule_version ENABLE ROW LEVEL SECURITY;

--
-- Name: scenario; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.scenario ENABLE ROW LEVEL SECURITY;

--
-- Name: scenario_impact; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.scenario_impact ENABLE ROW LEVEL SECURITY;

--
-- Name: scenario_result; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.scenario_result ENABLE ROW LEVEL SECURITY;

--
-- Name: scenario_template; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.scenario_template ENABLE ROW LEVEL SECURITY;

--
-- Name: side_effect; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.side_effect ENABLE ROW LEVEL SECURITY;

--
-- Name: store; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.store ENABLE ROW LEVEL SECURITY;

--
-- Name: submission_criterion; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.submission_criterion ENABLE ROW LEVEL SECURITY;

--
-- Name: sys_role_scope; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sys_role_scope ENABLE ROW LEVEL SECURITY;

--
-- Name: rescue_incident_w4_demo workspace_4_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY workspace_4_only ON public.rescue_incident_w4_demo USING (((ontology_id = 4) AND (ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint))) WITH CHECK (((ontology_id = 4) AND (ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)));


--
-- Name: rescue_team_w4_demo workspace_4_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY workspace_4_only ON public.rescue_team_w4_demo USING (((ontology_id = 4) AND (ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint))) WITH CHECK (((ontology_id = 4) AND (ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)));


--
-- Name: writeback_call; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.writeback_call ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict njo8d1fnYkstDKeDaufeewylqvDax1NmQNiQSB6lbedM2BAguT38SLPCrSPfHTD


--
-- PostgreSQL database dump
--

\restrict aGdabF056aGefy5XFHOjADEXsVm2gmpDLWg5emTorl0bWcnGSEU9uapjogZaccp

-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: maintenance_asset_demo_20261028; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.maintenance_asset_demo_20261028 (id, ontology_id, code, status, maintenance_at, lock_version, updated_at) FROM stdin;
2	8	EQ-002	已完成	2026-10-10 09:00:00+08	1	2026-10-28 17:15:30.000409+08
1	8	EQ-001	需要维护	2026-09-20 10:00:00+08	2	2026-10-28 18:07:57.818179+08
3	8	EQ-003	需要维护	2026-10-25 15:00:00+08	2	2026-10-28 18:27:38.949997+08
\.


--
-- Data for Name: maintenance_asset_extra_demo_20261028; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.maintenance_asset_extra_demo_20261028 (id, ontology_id, asset_id, calibration_note) FROM stdin;
1	8	1	校准合格
2	8	2	下次校准
3	8	3	已校准
\.


--
-- Data for Name: maintenance_task_demo_20261028; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.maintenance_task_demo_20261028 (id, ontology_id, asset_id, code, status, lock_version, updated_at) FROM stdin;
1	8	1	TASK-001	待处理	1	2026-10-28 17:45:47.125227+08
2	8	2	TASK-002	已完成	1	2026-10-28 17:45:47.125227+08
\.


--
-- Name: maintenance_asset_demo_20261028_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.maintenance_asset_demo_20261028_id_seq', 3, true);


--
-- Name: maintenance_task_demo_20261028_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.maintenance_task_demo_20261028_id_seq', 2, true);


--
-- PostgreSQL database dump complete
--

\unrestrict aGdabF056aGefy5XFHOjADEXsVm2gmpDLWg5emTorl0bWcnGSEU9uapjogZaccp
