-- Supply the institution prefix, including the trailing %, before running.
DECLARE @BranchPrefix nvarchar(128) = NULL;
IF @BranchPrefix IS NULL THROW 50000, 'Set @BranchPrefix explicitly', 1;

SELECT 'retail_scope' AS query_name, COUNT(*) AS warehouse_count
FROM dbo.Stock s
WHERE s.BranchID LIKE @BranchPrefix AND s.Deleted=0;

WITH stock AS (
 SELECT g.PtypeId, SUM(g.Qty) AS qty, SUM(g.Total) AS total
 FROM dbo.GoodsStocks g
 JOIN dbo.Stock s ON s.TypeID=g.KtypeId
 WHERE s.BranchID LIKE @BranchPrefix AND s.Deleted=0
 GROUP BY g.PtypeId
)
SELECT 'retail_product_inventory' AS query_name,
 COUNT(*) AS products_with_inventory_rows,
 SUM(CASE WHEN p.Deleted=0 THEN 1 ELSE 0 END) AS not_deleted,
 SUM(CASE WHEN p.Deleted=0 AND p.SonNum=0 THEN 1 ELSE 0 END) AS leaf_products,
 SUM(CASE WHEN p.Deleted=0 AND p.SonNum=0 AND st.qty<>0 THEN 1 ELSE 0 END) AS nonzero_qty,
 SUM(CASE WHEN p.Deleted=0 AND p.SonNum=0 AND st.qty>0 THEN 1 ELSE 0 END) AS positive_qty,
 SUM(CASE WHEN p.Deleted=0 AND p.SonNum=0 AND (st.qty<>0 OR st.total<>0) THEN 1 ELSE 0 END) AS qty_or_amount_nonzero
FROM stock st JOIN dbo.Ptype p ON p.TypeID=st.PtypeId;

WITH stock AS (
 SELECT g.PtypeId, SUM(g.Qty) AS qty, SUM(g.Total) AS total
 FROM dbo.GoodsStocks g
 JOIN dbo.Stock s ON s.TypeID=g.KtypeId
 WHERE s.BranchID LIKE @BranchPrefix AND s.Deleted=0
 GROUP BY g.PtypeId
)
SELECT TOP 8 p.UserCode,p.BarCode,p.FullName,st.qty,st.total,
 st.total/NULLIF(st.qty,0) AS candidate_weighted_inventory_price
FROM stock st JOIN dbo.Ptype p ON p.TypeID=st.PtypeId
WHERE p.Deleted=0 AND p.SonNum=0 AND st.qty>0 AND st.total>0
ORDER BY p.UserCode;
