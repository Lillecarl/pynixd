"""SQLite queries for the local store database.

Extracted from the old operation modules to break the dependency on the legacy
operation classes.  These are pure string constants used by
LocalDBStore for SQLite fast-path queries.
"""

IS_VALID_PATH = "SELECT 1 FROM ValidPaths WHERE path = ? LIMIT 1"

QUERY_PATH_INFO = """
SELECT path, deriver, hash, registrationTime, narSize, ultimate, sigs, ca
FROM ValidPaths WHERE path = ?
"""

QUERY_REFERENCES = """
SELECT vp.path FROM Refs r
JOIN ValidPaths vp ON r.reference = vp.id
WHERE r.referrer = (SELECT id FROM ValidPaths WHERE path = ?)
"""

QUERY_ALL_VALID_PATHS = "SELECT path FROM ValidPaths"

QUERY_VALID_PATHS = """
SELECT path FROM ValidPaths WHERE path IN (SELECT value FROM json_each(?))
"""

QUERY_PATH_FROM_HASH_PART = """
SELECT path FROM ValidPaths WHERE path >= ? AND path < ? LIMIT 1
"""

QUERY_CLOSURE = """
WITH RECURSIVE closure(id) AS (
    SELECT id FROM ValidPaths WHERE path IN (SELECT value FROM json_each(?))
    UNION
    SELECT r.reference
    FROM closure c
    JOIN Refs r ON c.id = r.referrer
)
SELECT vp.path FROM closure c
JOIN ValidPaths vp ON c.id = vp.id
"""

QUERY_CLOSURE_WITH_INFO = """
WITH RECURSIVE closure(id) AS (
    SELECT id FROM ValidPaths WHERE path IN (SELECT value FROM json_each(?))
    UNION
    SELECT r.reference
    FROM closure c
    JOIN Refs r ON c.id = r.referrer
)
SELECT vp.path, vp.deriver, vp.hash, vp.registrationTime, vp.narSize,
       vp.ultimate, vp.sigs, vp.ca,
       (SELECT group_concat(ref_vp.path, ' ')
        FROM Refs r
        JOIN ValidPaths ref_vp ON r.reference = ref_vp.id
        WHERE r.referrer = vp.id)
FROM closure c
JOIN ValidPaths vp ON c.id = vp.id
ORDER BY vp.id ASC
"""

QUERY_PATH_INFOS_BATCH = """
SELECT vp.path, vp.deriver, vp.hash, registrationTime, narSize,
       vp.ultimate, vp.sigs, vp.ca
FROM ValidPaths vp
WHERE vp.path IN (SELECT value FROM json_each(?))
"""

QUERY_REFERENCES_BATCH = """
SELECT vp_referrer.path, vp_ref.path
FROM Refs r
JOIN ValidPaths vp_referrer ON r.referrer = vp_referrer.id
JOIN ValidPaths vp_ref ON r.reference = vp_ref.id
WHERE vp_referrer.path IN (SELECT value FROM json_each(?))
"""

QUERY_DERIVATION_OUTPUT_MAP_BATCH = """
SELECT vp_drv.path, do.id, do.path
FROM DerivationOutputs do
JOIN ValidPaths vp_drv ON do.drv = vp_drv.id
WHERE vp_drv.path IN (SELECT value FROM json_each(?))
"""
