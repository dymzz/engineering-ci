$baseSha = (git rev-parse HEAD).Trim()
$baseRepo = (gh repo view --json nameWithOwner --jq '.nameWithOwner').Trim()


Write-Host "Repository: $baseRepo"
Write-Host "Commit: $baseSha"


