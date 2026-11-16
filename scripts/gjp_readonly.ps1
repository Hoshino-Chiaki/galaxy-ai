param([Parameter(Mandatory=$true)][string]$QueryFile)
$ErrorActionPreference='Stop'
$lines=Get-Content -LiteralPath 'C:\Users\Administrator\Desktop\新建文本文档 (4).txt'
function Field([string]$prefix) {
    $line=$lines | Where-Object { $_.StartsWith($prefix) } | Select-Object -First 1
    return ($line -split "`t",2)[1].Trim()
}
$builder=[System.Data.SqlClient.SqlConnectionStringBuilder]::new()
$builder['Data Source']=Field '服务器名称'
$builder['Initial Catalog']=Field '默认数据库'
$builder['User ID']=Field '登录名'
$builder['Password']=Field '密码'
$builder['Encrypt']=$true
$builder['TrustServerCertificate']=$true
$builder['ApplicationIntent']='ReadOnly'
$builder['Connect Timeout']=15
$builder['Application Name']='Galaxy cost investigation read only'
$connection=[System.Data.SqlClient.SqlConnection]::new($builder.ConnectionString)
try {
    $connection.Open()
    $command=$connection.CreateCommand()
    $command.CommandTimeout=45
    $command.CommandText=Get-Content -LiteralPath $QueryFile -Raw
    $reader=$command.ExecuteReader()
    try {
        $set=0
        do {
            $set++
            $count=0
            while($reader.Read()) {
                $count++
                if($count -le 35) {
                    $row=[ordered]@{result_set=$set}
                    for($i=0;$i -lt $reader.FieldCount;$i++) {
                        $value=$reader.GetValue($i)
                        $row[$reader.GetName($i)]=if($value -is [DBNull]){$null}else{$value}
                    }
                    $row | ConvertTo-Json -Compress -Depth 4
                }
            }
            Write-Output "RESULT_SET $set ROWS $count"
        } while($reader.NextResult())
    } finally {$reader.Close()}
} finally {$connection.Dispose()}
