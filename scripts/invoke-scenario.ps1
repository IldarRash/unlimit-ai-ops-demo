param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        'normal',
        'slow-provider',
        'provider-errors',
        'provider-timeout',
        'healthcheck-down',
        'healthcheck-timeout',
        'recover'
    )]
    [string]$Scenario,

    [ValidateSet('atlas-pay', 'nova-bank', 'orbit-wallet')]
    [string]$Provider = 'atlas-pay',

    [string]$BaseUrl = 'http://localhost:8000'
)

$uri = "$($BaseUrl.TrimEnd('/'))/admin/scenarios/$Scenario"
$body = @{ provider = $Provider } | ConvertTo-Json

$result = Invoke-RestMethod `
    -Method Post `
    -Uri $uri `
    -ContentType 'application/json' `
    -Body $body

$result | ConvertTo-Json -Depth 5
