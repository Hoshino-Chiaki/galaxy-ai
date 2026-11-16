SELECT TOP 30 SCHEMA_NAME(o.schema_id) AS schema_name,o.name,o.type_desc,
 CASE WHEN m.definition IS NULL THEN 0 ELSE 1 END AS definition_visible
FROM sys.objects o LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id
WHERE o.name LIKE '%GoodsStock%' OR o.name LIKE '%StockPrice%' OR o.name LIKE '%StockCost%'
ORDER BY o.name;

SELECT 'sql_definition_visibility' AS query_name,COUNT(*) AS module_count,
 SUM(CASE WHEN definition IS NOT NULL THEN 1 ELSE 0 END) AS visible_definitions
FROM sys.sql_modules;
