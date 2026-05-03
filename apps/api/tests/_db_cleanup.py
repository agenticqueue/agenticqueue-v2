from psycopg import Connection


def cleanup_actor_state(
    conn: Connection[tuple[object, ...]],
    *,
    actor_name_prefix: str,
) -> None:
    actor_like = f"{actor_name_prefix}%"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            """,
            (actor_like,),
        )
        cursor.execute(
            """
            DELETE FROM api_keys
            WHERE actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
               OR revoked_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (actor_like, actor_like),
        )
        cursor.execute("DELETE FROM actors WHERE name LIKE %s", (actor_like,))


def cleanup_cap03_state(
    conn: Connection[tuple[object, ...]],
    *,
    actor_name_prefix: str,
    project_slug_prefix: str,
) -> None:
    actor_like = f"{actor_name_prefix}%"
    project_like = f"{project_slug_prefix}%"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            """,
            (actor_like,),
        )
        cursor.execute(
            """
            DELETE FROM decisions
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR (
                    attached_to_kind = 'job'
                    AND attached_to_id IN (
                        SELECT jobs.id
                        FROM jobs
                        JOIN projects ON projects.id = jobs.project_id
                        WHERE projects.slug LIKE %s
                           OR projects.created_by_actor_id IN (
                                SELECT id FROM actors WHERE name LIKE %s
                           )
                    )
               )
            """,
            (actor_like, project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM learnings
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR (
                    attached_to_kind = 'job'
                    AND attached_to_id IN (
                        SELECT jobs.id
                        FROM jobs
                        JOIN projects ON projects.id = jobs.project_id
                        WHERE projects.slug LIKE %s
                           OR projects.created_by_actor_id IN (
                                SELECT id FROM actors WHERE name LIKE %s
                           )
                    )
               )
            """,
            (actor_like, project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM job_comments
            WHERE author_actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
               OR job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
                       OR projects.created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (actor_like, project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM job_edges
            WHERE from_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
                       OR projects.created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
               OR to_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
                       OR projects.created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (project_like, actor_like, project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM jobs
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR claimed_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id
                    FROM projects
                    WHERE slug LIKE %s
                       OR created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (actor_like, actor_like, project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM labels
            WHERE project_id IN (
                SELECT id
                FROM projects
                WHERE slug LIKE %s
                   OR created_by_actor_id IN (
                        SELECT id FROM actors WHERE name LIKE %s
                   )
            )
            """,
            (project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM pipelines
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id
                    FROM projects
                    WHERE slug LIKE %s
                       OR created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (actor_like, project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM projects
            WHERE slug LIKE %s
               OR created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (project_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM api_keys
            WHERE actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
               OR revoked_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (actor_like, actor_like),
        )
        cursor.execute("DELETE FROM actors WHERE name LIKE %s", (actor_like,))
