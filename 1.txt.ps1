$rejected = $false

try {
    dagger check fixture-lint
    $rejected = ($LASTEXITCODE -ne 0)
}
finally {
    Remove-Item $probe -ErrorAction SilentlyContinue
}

if ($rejected) {
    Write-Host "PASS: Ruff error correctly rejected"
} else {
    throw "FAIL: CI did not reject the Ruff error"
}