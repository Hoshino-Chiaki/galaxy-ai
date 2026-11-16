-- Supply the institution prefix, including the trailing %, before running.
DECLARE @BranchPrefix nvarchar(128) = NULL;
IF @BranchPrefix IS NULL THROW 50000, 'Set @BranchPrefix explicitly', 1;

WITH stock AS (
 SELECT g.PtypeId, SUM(g.Qty) AS qty, SUM(g.Total) AS total, SUM(g.WayQty) AS wayqty,
        COUNT(*) AS warehouse_rows, MIN(g.Price) AS min_stored_price,MAX(g.Price) AS max_stored_price
 FROM dbo.GoodsStocks g JOIN dbo.Stock s ON s.TypeID=g.KtypeId
 WHERE s.BranchID LIKE @BranchPrefix AND s.Deleted=0
 GROUP BY g.PtypeId
)
SELECT 'retail_scope_filters' AS query_name,COUNT(*) AS all_products,
 SUM(CASE WHEN qty<>0 THEN 1 ELSE 0 END) AS nonzero_qty,
 SUM(CASE WHEN qty<>0 OR wayqty<>0 THEN 1 ELSE 0 END) AS qty_or_transit,
 SUM(CASE WHEN qty+wayqty<>0 THEN 1 ELSE 0 END) AS qty_plus_transit,
 SUM(CASE WHEN qty<>0 OR total<>0 OR wayqty<>0 THEN 1 ELSE 0 END) AS qty_amount_or_transit,
 SUM(CASE WHEN total>0 AND qty=0 THEN 1 ELSE 0 END) AS zero_qty_positive_amount,
 SUM(CASE WHEN warehouse_rows>1 AND min_stored_price<>max_stored_price THEN 1 ELSE 0 END) AS different_warehouse_prices
FROM stock st JOIN dbo.Ptype p ON p.TypeID=st.PtypeId
WHERE p.Deleted=0 AND p.SonNum=0;

WITH stock AS (
 SELECT g.PtypeId,SUM(g.Qty) qty,SUM(g.Total) total,SUM(g.WayQty) wayqty
 FROM dbo.GoodsStocks g JOIN dbo.Stock s ON s.TypeID=g.KtypeId
 WHERE s.BranchID LIKE @BranchPrefix
 GROUP BY g.PtypeId
)
SELECT 'including_deleted_warehouses' AS query_name,
 SUM(CASE WHEN qty<>0 THEN 1 ELSE 0 END) AS nonzero_qty,
 SUM(CASE WHEN qty<>0 OR total<>0 THEN 1 ELSE 0 END) AS qty_or_amount,
 SUM(CASE WHEN qty<>0 OR total<>0 OR wayqty<>0 THEN 1 ELSE 0 END) AS qty_amount_or_transit
FROM stock st JOIN dbo.Ptype p ON p.TypeID=st.PtypeId WHERE p.Deleted=0 AND p.SonNum=0;
