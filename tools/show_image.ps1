# 画像を WezTerm の右隣のペインに wezterm imgcat で表示する。
# 右隣のペインは画像表示専用で、シェルは PowerShell である前提。
param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$Paths
)

$ErrorActionPreference = 'Stop'

if (-not $Paths) {
    Write-Error '使い方: pwsh -NoProfile -File tools/show_image.ps1 <png> [<png> ...]'
    exit 1
}

# 送り先のペインはカレントディレクトリが異なるため絶対パスにする
$absPaths = $Paths | ForEach-Object { [System.IO.Path]::GetFullPath($_, $PWD.Path) }

if (-not $env:WEZTERM_PANE) {
    [Console]::Error.WriteLine('WezTerm の外で実行されているため表示できない。画像のパス:')
    $absPaths | ForEach-Object { [Console]::Error.WriteLine($_) }
    exit 1
}

$imgcat = ($absPaths | ForEach-Object { "wezterm imgcat '$($_.Replace("'", "''"))'" }) -join '; '

$right = (wezterm cli get-pane-direction right --pane-id $env:WEZTERM_PANE | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Write-Error "wezterm cli get-pane-direction が失敗した (終了コード $LASTEXITCODE)"
    exit 1
}

if ($right) {
    # 先頭の Esc は入力途中の行を消すため (PSReadLine では Esc で行がクリアされる)
    $text = "`e" + "Clear-Host; $imgcat" + "`r"
    wezterm cli send-text --pane-id $right --no-paste $text
    if ($LASTEXITCODE -ne 0) {
        Write-Error "wezterm cli send-text が失敗した (終了コード $LASTEXITCODE)"
        exit 1
    }
    $paneId = $right
} else {
    $paneId = (wezterm cli split-pane --right --percent 50 -- pwsh -NoProfile -Command "$imgcat; Read-Host 'Enter で閉じる'" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Error "wezterm cli split-pane が失敗した (終了コード $LASTEXITCODE)"
        exit 1
    }
}

Write-Output $paneId
