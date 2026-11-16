-- Supply the institution prefix, including the trailing %, before running.
DECLARE @BranchPrefix nvarchar(128) = NULL;
IF @BranchPrefix IS NULL THROW 50000, 'Set @BranchPrefix explicitly', 1;
DECLARE @SampleCodes TABLE (Code nvarchar(128) PRIMARY KEY);
-- INSERT INTO @SampleCodes VALUES (N'<sample barcode>');
IF NOT EXISTS (SELECT 1 FROM @SampleCodes) THROW 50001, 'Supply sample barcodes', 1;

SELECT 'sample_identification' AS query_name,p.TypeID,p.UserCode,p.BarCode,p.FullName,p.SonNum,p.Deleted,p.CostMode,p.Unit1,p.UnitRate1,p.UnitRate2
FROM dbo.Ptype p
WHERE p.BarCode IN (SELECT Code FROM @SampleCodes)
   OR p.UserCode IN (SELECT Code FROM @SampleCodes)
ORDER BY p.UserCode,p.TypeID;

WITH target AS (
 SELECT p.TypeID,p.UserCode,p.BarCode,p.FullName
 FROM dbo.Ptype p
 WHERE p.BarCode IN (SELECT Code FROM @SampleCodes)
    OR p.UserCode IN (SELECT Code FROM @SampleCodes)
)
SELECT 'sample_scope_aggregation' AS query_name,t.TypeID,t.UserCode,t.BarCode,t.FullName,
 CASE WHEN s.BranchID LIKE @BranchPrefix THEN 'retail' ELSE 'other_institutions' END AS scope_name,
 COUNT(*) AS inventory_rows,SUM(g.Qty) AS qty,SUM(g.Total) AS amount,SUM(g.WayQty) AS transit_qty,
 SUM(g.Total)/NULLIF(SUM(g.Qty),0) AS weighted_inventory_cost,
 MIN(g.Price) AS min_stored_price,MAX(g.Price) AS max_stored_price,
 AVG(g.Price) AS unweighted_stored_price,
 SUM(CASE WHEN g.Qty<>0 THEN g.Total ELSE 0 END)/NULLIF(SUM(g.Qty),0) AS weighted_excluding_zero_quantity_amount
FROM target t JOIN dbo.GoodsStocks g ON g.PtypeId=t.TypeID JOIN dbo.Stock s ON s.TypeID=g.KtypeId
WHERE s.Deleted=0
GROUP BY t.TypeID,t.UserCode,t.BarCode,t.FullName,CASE WHEN s.BranchID LIKE @BranchPrefix THEN 'retail' ELSE 'other_institutions' END
ORDER BY t.UserCode,scope_name;

SELECT 'sample_retail_warehouse_rows' AS query_name,p.UserCode,p.BarCode,s.TypeID AS warehouse_id,s.FullName AS warehouse_name,s.BranchID,
 g.Qty,g.Total,g.Price,g.WayQty,g.Total/NULLIF(g.Qty,0) AS warehouse_amount_divided_by_quantity
FROM dbo.Ptype p JOIN dbo.GoodsStocks g ON g.PtypeId=p.TypeID JOIN dbo.Stock s ON s.TypeID=g.KtypeId
WHERE (p.BarCode IN (SELECT Code FROM @SampleCodes)
    OR p.UserCode IN (SELECT Code FROM @SampleCodes))
 AND s.BranchID LIKE @BranchPrefix AND s.Deleted=0
ORDER BY p.UserCode,s.TypeID;

SELECT 'sample_reference_prices' AS query_name,p.UserCode,p.BarCode,pp.PriceTypeid,pp.Price,pp.branchid
FROM dbo.Ptype p JOIN dbo.PtypePrice pp ON pp.Ptypeid=p.TypeID
WHERE (p.BarCode IN (SELECT Code FROM @SampleCodes)
    OR p.UserCode IN (SELECT Code FROM @SampleCodes))
 AND pp.PriceTypeid IN ('CKCB','YSCGJ1','LSSJ1')
ORDER BY p.UserCode,pp.PriceTypeid;
