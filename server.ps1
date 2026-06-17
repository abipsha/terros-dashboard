# Vivid Terros Dashboard — Local Proxy Server
# No install needed: uses built-in Windows PowerShell

$ApiKey  = 'atQjJCg13du0c4aAeU4hc'
$ApiBase = 'https://api.terros.com'
$Port    = 3000
$Dir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Url     = "http://localhost:$Port/vivid-terros-dashboard.html"

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")

try { $listener.Start() }
catch {
    Write-Host "`n  ERROR: Could not start on port $Port. Is another instance running?" -ForegroundColor Red
    Read-Host "`n  Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "  Vivid Terros Dashboard" -ForegroundColor Green
Write-Host "  $Url" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Press Ctrl+C to stop."
Write-Host ""

Start-Process $Url

function Send-Response($response, $statusCode, $contentType, $bytes) {
    $response.StatusCode = $statusCode
    $response.ContentType = $contentType
    $response.Headers.Add('Access-Control-Allow-Origin', '*')
    $response.ContentLength64 = $bytes.Length
    $response.OutputStream.Write($bytes, 0, $bytes.Length)
    $response.Close()
}

while ($listener.IsListening) {
    try { $ctx = $listener.GetContext() } catch { break }
    $req  = $ctx.Request
    $resp = $ctx.Response
    $path = $req.Url.AbsolutePath
    $method = $req.HttpMethod

    Write-Host "  $method  $path"

    # ── CORS preflight ───────────────────────────────────────────
    if ($method -eq 'OPTIONS') {
        $resp.Headers.Add('Access-Control-Allow-Origin',  '*')
        $resp.Headers.Add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        $resp.Headers.Add('Access-Control-Allow-Headers', 'Content-Type')
        $resp.StatusCode = 200
        $resp.Close()
        continue
    }

    # ── Proxy POST /api/* → Terros ───────────────────────────────
    if ($method -eq 'POST' -and $path.StartsWith('/api/')) {
        $target  = $ApiBase + $path.Substring(4)
        $reader  = New-Object System.IO.StreamReader($req.InputStream)
        $body    = $reader.ReadToEnd()
        $reader.Close()

        try {
            $wr = [System.Net.WebRequest]::Create($target)
            $wr.Method      = 'POST'
            $wr.ContentType = 'application/json'
            $wr.Headers.Add('Authorization', "ApiKey $ApiKey")
            $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
            $wr.ContentLength = $bodyBytes.Length
            $s = $wr.GetRequestStream(); $s.Write($bodyBytes, 0, $bodyBytes.Length); $s.Close()

            $wr2    = $wr.GetResponse()
            $rdr    = New-Object System.IO.StreamReader($wr2.GetResponseStream())
            $result = [System.Text.Encoding]::UTF8.GetBytes($rdr.ReadToEnd())
            $rdr.Close(); $wr2.Close()
            Send-Response $resp 200 'application/json' $result
        }
        catch [System.Net.WebException] {
            $errResp = $_.Exception.Response
            if ($errResp) {
                $rdr  = New-Object System.IO.StreamReader($errResp.GetResponseStream())
                $errB = [System.Text.Encoding]::UTF8.GetBytes($rdr.ReadToEnd())
                $rdr.Close()
                Send-Response $resp ([int]$errResp.StatusCode) 'application/json' $errB
            } else {
                $errB = [System.Text.Encoding]::UTF8.GetBytes('{"error":"Network error"}')
                Send-Response $resp 502 'application/json' $errB
            }
        }
        continue
    }

    # ── Serve static files ───────────────────────────────────────
    if ($method -eq 'GET') {
        $rel = if ($path -eq '/' -or $path -eq '') { 'vivid-terros-dashboard.html' } else { $path.TrimStart('/') }
        $file = Join-Path $Dir $rel

        if (Test-Path $file -PathType Leaf) {
            $ext  = [System.IO.Path]::GetExtension($file).ToLower()
            $mime = switch ($ext) {
                '.html' { 'text/html; charset=utf-8' }
                '.js'   { 'application/javascript' }
                '.css'  { 'text/css' }
                default { 'application/octet-stream' }
            }
            Send-Response $resp 200 $mime ([System.IO.File]::ReadAllBytes($file))
        } else {
            $b = [System.Text.Encoding]::UTF8.GetBytes('Not found')
            Send-Response $resp 404 'text/plain' $b
        }
        continue
    }

    $resp.StatusCode = 405; $resp.Close()
}

$listener.Stop()
