<#
RoadPush 백엔드 배포 (PC에서 한 번 실행). RoadPush_BE와 RoadPush_AI가 같은 상위 폴더(C:\dev)에 있어야 한다.

  .\deploy\deploy.ps1 -Server 1.2.3.4 -Key C:\keys\oracle.key                 # 백엔드 + AI 코드·모델
  .\deploy\deploy.ps1 -Server 1.2.3.4 -Key ... -SkipModels                     # 모델은 그대로 (코드만)
  .\deploy\deploy.ps1 -Server 1.2.3.4 -Key ... -SkipAi                         # 백엔드만
  .\deploy\deploy.ps1 -Server 1.2.3.4 -Key ... -WithStorage                    # PC storage/ 사진·overlay도
  .\deploy\deploy.ps1 -Server 1.2.3.4 -Key ... -CheckModel                     # 끝나고 서버에서 check_model.py
  .\deploy\deploy.ps1 -Server 1.2.3.4 -DryRun                                  # 묶기만 하고 보내지 않음

하는 일
1. 백엔드: 커밋 안 된 변경이 없고 GitHub(origin/main)와 같은지 확인 (서버는 GitHub에서 git pull 한다)
2. AI: 커밋 번호·변경 여부를 VERSION.json으로 남기고 실행에 필요한 파일만 묶는다 (.git·인증 정보는 보내지 않음)
3. 서버로 보내고 deploy/server_update.sh 실행: AI 풀기 → git pull → 패키지 → 재시작 → health
4. https://<도메인>/api/v1/health의 backend·ai 커밋이 PC와 같은지 확인

처음 설치(서버 계정·venv·systemd·Caddy·.env)는 docs/DEPLOY.md 3장대로 한 번 해 둔다.
#>
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Key,
    [string]$User = "ubuntu",
    [string]$Domain,                # 기본: <IP>.sslip.io (점을 -로)
    [switch]$SkipAi,
    [switch]$SkipModels,
    [switch]$WithStorage,
    [switch]$CheckModel,
    [switch]$AllowDirtyAi,          # AI 저장소에 커밋 안 된 변경이 있어도 보낸다 (VERSION.json에 dirty=true)
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"

$Be = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Root = Split-Path $Be
$Ai = Join-Path $Root "RoadPush_AI"
if (-not $Domain) { $Domain = ($Server -replace '\.', '-') + ".sslip.io" }
$Stage = Join-Path $env:TEMP ("roadpush_deploy_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Force $Stage | Out-Null

function RepoGit([string]$Repo) { & git.exe -C $Repo @args; if ($LASTEXITCODE) { throw "git 실패: $Repo $args" } }
function Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
# DryRun이면 확인 실패를 경고로만 (묶음이 제대로 만들어지는지 먼저 볼 때)
function Fail([string]$Text) { if ($DryRun) { Write-Warning $Text } else { throw $Text } }

# ── 1. 백엔드 확인 ────────────────────────────────────────────
Step "1. 백엔드 확인"
if (RepoGit $Be status --porcelain --untracked-files=no) { Fail "백엔드에 커밋 안 된 변경이 있습니다. 커밋·push 후 다시 실행하세요" }
RepoGit $Be fetch -q origin
$BeHead = RepoGit $Be rev-parse HEAD
$BeRemote = RepoGit $Be rev-parse origin/main
if ($BeHead -ne $BeRemote) { Fail "백엔드 HEAD($($BeHead.Substring(0,7)))가 origin/main($($BeRemote.Substring(0,7)))과 다릅니다. push 또는 pull 하세요" }
Write-Host "backend $($BeHead.Substring(0,7)) = origin/main"

# ── 2. AI 묶기 ────────────────────────────────────────────────
$Files = @()
$Remote = @()
$AiCommit = $null
if (-not $SkipAi) {
    Step "2. AI 코드 묶기"
    $AiCommit = RepoGit $Ai rev-parse HEAD
    $AiDirty = [bool](RepoGit $Ai status --porcelain --untracked-files=no)
    if ($AiDirty -and -not $AllowDirtyAi) { Fail "AI 저장소에 커밋 안 된 변경이 있습니다. 커밋하거나 -AllowDirtyAi" }
    $IncludeModels = -not $SkipModels
    if ($IncludeModels -and -not (Test-Path (Join-Path $Ai "models"))) { throw "RoadPush_AI\models가 없습니다 (-SkipModels로 코드만 보낼 수 있음)" }

    New-Item -ItemType Directory -Force (Join-Path $Stage "RoadPush_AI") | Out-Null
    $Version = [ordered]@{
        commit      = $AiCommit
        dirty       = $AiDirty
        branch      = (RepoGit $Ai rev-parse --abbrev-ref HEAD)
        packaged_at = (Get-Date).ToString("o")
        models      = $IncludeModels
    }
    # BOM 없는 UTF-8 (서버 Python json이 읽음)
    [IO.File]::WriteAllText((Join-Path $Stage "RoadPush_AI\VERSION.json"), ($Version | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))

    $Parts = @("RoadPush_AI/src", "RoadPush_AI/pyproject.toml", "RoadPush_AI/configs", "RoadPush_AI/schemas",
        "RoadPush_AI/surface_passability/src", "RoadPush_AI/surface_passability/pyproject.toml",
        "RoadPush_AI/surface_passability/manifests", "RoadPush_AI/surface_passability/schemas")
    if ($IncludeModels) { $Parts += "RoadPush_AI/models" }
    $AiTgz = Join-Path $Stage "roadpush_ai.tgz"
    & tar -czf $AiTgz --exclude=__pycache__ -C $Root @Parts -C $Stage "RoadPush_AI/VERSION.json"
    if ($LASTEXITCODE) { throw "AI 묶기 실패" }
    Write-Host ("ai {0}{1} · 모델 {2} · {3:N0} MB" -f $AiCommit.Substring(0, 7), $(if ($AiDirty) { " (dirty)" } else { "" }),
        $(if ($IncludeModels) { "포함" } else { "제외" }), ((Get-Item $AiTgz).Length / 1MB))
    $Files += $AiTgz
    $Remote += "--ai ~/roadpush_deploy/roadpush_ai.tgz"
}

if ($WithStorage) {
    Step "2-1. 사진·overlay 묶기 (storage/)"
    $StTgz = Join-Path $Stage "storage.tgz"
    & tar -czf $StTgz -C $Be "storage/observations" "storage/analyses"
    if ($LASTEXITCODE) { throw "storage 묶기 실패" }
    Write-Host ("storage {0:N0} MB" -f ((Get-Item $StTgz).Length / 1MB))
    $Files += $StTgz
    $Remote += "--storage ~/roadpush_deploy/storage.tgz"
}
if ($CheckModel) { $Remote += "--check-model" }

$Files += (Join-Path $PSScriptRoot "server_update.sh")
$Ssh = @("-o", "StrictHostKeyChecking=accept-new")
if ($Key) { $Ssh += @("-i", $Key) }
$Target = "$User@$Server"
$Cmd = "bash ~/roadpush_deploy/server_update.sh " + ($Remote -join " ")

if ($DryRun) {
    Step "DryRun: 보내지 않음"
    Write-Host "묶음 폴더: $Stage"
    Write-Host "scp $($Files -join ' ') ${Target}:~/roadpush_deploy/"
    Write-Host "ssh $Target `"$Cmd`""
    exit 0
}

# ── 3. 보내고 서버에서 갱신 ───────────────────────────────────
Step "3. 서버로 보내기 ($Target)"
& ssh @Ssh $Target "mkdir -p ~/roadpush_deploy"
if ($LASTEXITCODE) { throw "ssh 접속 실패" }
& scp @Ssh @Files "${Target}:~/roadpush_deploy/"
if ($LASTEXITCODE) { throw "scp 실패" }
Step "3-1. 서버 갱신"
& ssh @Ssh $Target $Cmd
if ($LASTEXITCODE) { throw "서버 갱신 실패 (위 로그 확인)" }

# ── 4. 공개 주소 확인 ─────────────────────────────────────────
Step "4. https://$Domain/api/v1/health"
$H = Invoke-RestMethod "https://$Domain/api/v1/health" -TimeoutSec 30
$H | ConvertTo-Json -Depth 4 | Write-Host
if ($H.backend.commit -and $H.backend.commit -ne $BeHead) { throw "서버 backend 커밋($($H.backend.commit))이 PC와 다릅니다" }
if ($AiCommit -and $H.ai.commit -ne $AiCommit) { throw "서버 ai 커밋($($H.ai.commit))이 PC와 다릅니다" }
Remove-Item -Recurse -Force $Stage
Write-Host "`n[OK] 배포 완료 · backend $($BeHead.Substring(0,7))$(if ($AiCommit) { ' · ai ' + $AiCommit.Substring(0,7) })" -ForegroundColor Green
