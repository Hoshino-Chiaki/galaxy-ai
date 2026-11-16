-- Supply the institution prefix, including the trailing %, before running.
DECLARE @BranchPrefix nvarchar(128) = NULL;
IF @BranchPrefix IS NULL THROW 50000, 'Set @BranchPrefix explicitly', 1;

WITH inventory AS (
 SELECT g.PtypeId,SUM(g.Qty) AS qty,SUM(g.Total) AS amount,
 SUM(CASE WHEN g.Qty>0 THEN 1 ELSE 0 END) AS positive_warehouse_count,
 MIN(CASE WHEN g.Qty>0 THEN g.Price END) AS min_positive_price,
 MAX(CASE WHEN g.Qty>0 THEN g.Price END) AS max_positive_price,
 AVG(CASE WHEN g.Qty>0 THEN g.Price END) AS unweighted_positive_price,
 SUM(CASE WHEN g.Qty=0 THEN g.Total ELSE 0 END) AS zero_quantity_amount
 FROM dbo.GoodsStocks g JOIN dbo.Stock s ON s.TypeID=g.KtypeId
 WHERE s.BranchID LIKE @BranchPrefix AND s.Deleted=0
 GROUP BY g.PtypeId
)
SELECT TOP 3 p.UserCode,p.BarCode,p.FullName,i.qty,i.amount,i.amount/NULLIF(i.qty,0) AS candidate_weighted_inventory_cost,
 i.positive_warehouse_count,i.min_positive_price,i.max_positive_price,i.unweighted_positive_price
FROM inventory i JOIN dbo.Ptype p ON p.TypeID=i.PtypeId
WHERE p.Deleted=0 AND p.SonNum=0 AND i.positive_warehouse_count>1 AND i.qty>0 AND i.amount>0
 AND i.max_positive_price-i.min_positive_price>1 AND i.zero_quantity_amount=0
 AND LEN(p.BarCode)=13
 AND ABS(i.amount/NULLIF(i.qty,0)-i.unweighted_positive_price)>1
ORDER BY p.UserCode;
