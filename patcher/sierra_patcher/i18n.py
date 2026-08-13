from __future__ import annotations

import json
import locale
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Iterable


DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "ko": "한국어",
}
LANGUAGE_ENV = "SIERRA_PATCHER_LANGUAGE"


_KO = {
    # Application shell
    "Language": "언어",
    "Generate": "생성",
    "Install": "설치",
    "Logs": "로그",
    "Info": "정보",
    "Progress": "진행 상황",
    "Idle": "대기 중",
    "Preparing generation": "생성 준비 중",
    "Preparing installation": "설치 준비 중",
    "Validating resources...": "필요한 리소스를 확인하는 중...",
    "Validating package...": "패키지를 확인하는 중...",
    "A task is running. Wait for it to finish or cancel it before changing the language.":
        "작업이 진행 중 입니다. 완료될 때까지 기다리거나 취소한 뒤 언어를 바꿔 주세요.",
    "The language changed for this session, but the preference could not be saved:\n{error}":
        "이번 실행의 언어는 바뀌었지만 설정을 저장하지 못했습니다:\n{error}",
    "The language could not be changed:\n{error}":
        "언어를 변경 실패:\n{error}",

    # Common controls and validation
    "Browse": "찾아보기",
    "Browse...": "찾아보기...",
    "Refresh": "새로 고침",
    "Clear": "지우기",
    "Abort": "중단",
    "Cancel": "취소",
    "Open destination": "대상 폴더 열기",
    "Select folder": "폴더 선택",
    "Destination folder is required.": "대상 폴더를 선택하십시오.",
    "Folder does not exist.": "폴더가 존재하지 않습니다.",
    "REQUIRED": "필수",
    "READY  ✓": "준비됨  ✓",
    "UPDATE LIVE  ⚠": "본섭 업데이트  ⚠",
    "PATCH UPDATE  ⚠": "패치 업데이트  ⚠",
    "FOLDER MISMATCH  ⚠": "폴더 불일치  ⚠",
    "VERSION UNKNOWN  ⚠": "확인 불가  ⚠",
    "UNVERIFIED  ⚠": "미확인  ⚠",
    "CHECKING...": "확인 중...",
    "Not found": "찾을 수 없음",
    "not found": "찾을 수 없음",
    "error": "오류",
    "Stopped": "중지됨",
    "Cancelled": "취소됨",
    "Done": "완료",

    # Install screen
    "Package source": "패키지 소스",
    "Source": "소스",
    "Local package": "로컬 패키지",
    "Web release": "웹 릴리스",
    "Archived snapshot": "보관 스냅샷",
    "Archived: {package_id}": "보관 스냅샷: {package_id}",
    "Release ID": "릴리스 ID",
    "Version / Release": "버전 / 릴리스",
    "Cache directory": "캐시 폴더",
    "Download workers": "동시 다운로드 수",
    "Reconstruction workers": "동시 재구성 수",
    "Verified objects and completed package files are retained for resume/reuse.":
        "확인된 파일과 완성된 패키지는 이어받기와 재사용을 위해 보관됩니다.",
    "Installation": "설치",
    "Destination to patch": "패치할 대상 폴더",
    "Select pasted Live folder": "복사해 둔 라이브 폴더 선택",
    "Patch threads": "패치 스레드 수",
    "Force (bypass metadata checks)": "강제 진행 (메타데이터 검사 건너뛰기)",
    "Install SPT": "SPT 설치",
    "Status": "상태",
    "System": "시스템",
    "Patcher": "패처",
    "Tarkov": "타르코프",
    "Live Tarkov Folder": "본섭 타르코프 폴더",
    "Destination": "대상",
    "CPU": "CPU",
    "Cores": "코어",
    "Memory": "메모리",
    "{physical} cores / {logical} threads": "{physical}코어 / {logical}스레드",
    "{total} total, {available} free": "전체 {total}, 여유 {available}",
    "Client": "클라이언트",
    "Release": "릴리스",
    "Patches": "패치 수",
    "Path": "경로",
    "Version": "버전",
    "Publisher": "게시자",
    "Free": "여유 공간",
    "Select release": "릴리스 선택",
    "Web release: {release}": "웹 릴리스: {release}",
    "Choose version": "버전을 선택하십시오",
    "choose version": "버전 선택",
    "Not prepared": "준비되지 않음",
    "Supported {required} · Current {current}": "지원 {required} · 현재 {current}",
    "Found {destination} · Required {required}": "대상 {destination} · 필요 {required}",
    "Couldn’t read game version": "게임 버전을 읽지 못했어요",
    "No version data · Checked after download": "버전 정보 없음 · 다운로드 후 확인",
    "Checking release compatibility...": "릴리스 호환성을 확인하고 있어요...",
    "Advanced ▸": "고급 설정 ▸",
    "Advanced ▾": "고급 설정 ▾",
    "Select web package cache": "웹 패키지 캐시 폴더 선택",
    "These settings normally do not need to be changed. Downloaded cache is removed automatically after a successful web installation.":
        "고급 설정은 꼭 필요한 경우가 아니면 변경 할 필요 없음. 웹 설치가 끝나면 다운로드 캐시는 자동으로 삭제됨.",
    "Version selection is required.": "설치할 버전을 선택하시오.",
    "Loading available versions...": "설치 가능한 버전 불러오는 중...",
    "Could not load versions. Check repository catalog.json.":
        "버전 목록을 불러오기 실패. 서버 유지보수/패치파일 업데이트 중 혹은 네트워크 이상.",
    "No web releases are currently listed.": "현재 설치 가능한 패치 없음. 서버 유지보수/패치파일 업데이트 중 혹은 네트워크 이상.",
    "Select a Sierra Archived snapshot folder.": "패치파일을 저장할 위치를 선택하시오.",
    "Select a valid Sierra Archived snapshot folder.": "로컬 패치 탐지 실패.",
    "Archived snapshot is ready.": "로컬 패치파일 탐지 성공, 사용 가능.",
    "Save selected release as Archived snapshot...": "해당 패치 로컬에 저장",

    # Dialogs and install progress
    "Destination required": "대상 폴더 필요",
    "Select the pasted Live folder that needs to be patched.":
        "Sierra Patcher가 변경할, 복사해 둔 라이브 폴더를 선택해 주세요.",
    "Missing folder": "폴더 필요",
    "Select a valid destination folder.": "올바른 대상 폴더를 선택하세요.",
    "Please set Destination folder.": "대상 폴더를 지정하시오.",
    "Metadata error": "메타데이터 오류",
    "Could not read patch metadata:\n{error}": "패치 메타데이터를 읽지 못함:\n{error}",
    "Release required": "릴리스 필요",
    "Choose a local repository release first.": "로컬 저장소 릴리스를 선택 필요.",
    "Enter the web Release ID to install.": "설치할 웹 릴리스 ID를 입력 필요.",
    "Version required": "버전 필요",
    "Choose a web release version first.": "웹 릴리스 버전을 선택하십시오.",
    "Choose a web release to archive first.": "보관할 웹 릴리스를 선택하십시오.",
    "Invalid setting": "잘못된 설정값",
    "{label} must be a whole number": "{label} 값은 정수여야 함.",
    "{label} must be between 1 and {maximum}": "{label} 값은 1~{maximum} 사이여야 함.",
    "Fetching manifest": "매니페스트 가져오는 중",
    "Downloading objects": "파일 다운로드 중",
    "Reconstructing package": "패키지 재구성 중",
    "Publishing web package": "웹 패키지 게시 중",
    "Verifying archived objects": "보관된 파일 확인 중",
    "Resuming archived snapshot": "보관 스냅샷 이어받는 중",
    "Applying patches": "패치 적용 중",
    "Retrying failed patches": "실패한 패치 다시 시도 중",
    "Verifying patches": "패치 확인 중",
    "Finalizing": "마무리 중",
    "Finalizing (delete list)": "마무리 중 (삭제 목록)",
    "Applying storage": "추가 파일 적용 중",
    "Cleaning download cache": "다운로드 캐시 정리 중",
    "Removing downloaded patch data...": "다운로드한 패치 데이터를 삭제하는 중...",
    "cleanup done": "정리 완료",
    "Patch applied successfully.": "패치 성공적으로 적용 완료.",
    "Install failed. See Logs for details.": "설치 실패. 자세한 내용은 로그를 확인해 주세요.",
    "Patch package ready in:\n{path}": "패치 패키지가 준비완료:\n{path}",
    "Version mismatch": "버전 불일치",
    "Compatibility check": "호환성 확인",
    "No patch data was downloaded.": "패치 데이터는 다운로드하지 않았어요.",
    "Version mismatch detected.\n\nLive client: {live_version}\nExpected: {expected_version}\n\nIf your live version exceeds that of the patch, please wait for an update. Otherwise, please update your live game and try again.":
        "버전이 일치하지 않음.\n\n라이브 클라이언트: {live_version}\n필요한 버전: {expected_version}\n\n올바른 폴더를 선택하십시오. 본섭이 버전이 높다면 패치 업데이트를 기다리시고 본섭 버전이 낮다면 본섭 업데이트 후 다시 설치 과정을 시도하세요.",
    "Folder contents mismatch": "폴더 내용 불일치",
    "The destination differs from the source used to build this patch.\n\n{details}":
        "선택한 폴더가 이 패치를 만들 때 사용한 원본과 다릅니다. 무결성 검사 후 다시 시도하시기 바랍니다.\n\n{details}",
    "{relative}: expected {expected:,} bytes, found {actual:,} bytes":
        "{relative}: 예상 {expected:,}바이트, 실제 {actual:,}바이트",
    "Verifying source files": "원본 파일 확인 중",
    "Source files mismatch": "원본 파일 불일치",
    "The selected Tarkov copy does not match the source files required by this release.":
        "선택한 타르코프 복사본이 이 릴리스에 필요한 원본 파일과 일치하지 않습니다.",
    "Checked: {count}": "검사한 파일: {count}",
    "Matched: {count}": "일치: {count}",
    "Mismatched: {count}": "불일치: {count}",
    "No game files were modified.": "대상 폴더의 파일은 변경되지 않았습니다.",
    "Examples:": "예시:",
    "{path}: missing": "{path}: 파일 없음",
    "{path}: size mismatch (expected {expected:,}, found {actual:,} bytes)":
        "{path}: 크기 불일치 (예상 {expected:,}바이트, 실제 {actual:,}바이트)",
    "{path}: SHA-256 mismatch (expected {expected_sha}, found {actual_sha})":
        "{path}: SHA-256 불일치 (예상 {expected_sha}, 실제 {actual_sha})",
    "... and {count} more. See Logs for details.":
        "... 외 {count}개가 더 있습니다. 자세한 내용은 로그를 확인해 주세요.",
    "Cache cleanup": "캐시 정리",
    "The patch installed successfully, but Sierra Patcher could not remove all downloaded cache files.\n\nCache location:\n{cache_root}\n\nYou can delete the objects, packages, and manifests folders manually after closing the patcher.":
        "패치는 성공적으로 설치했지만 다운로드 캐시 일부를 삭제하지 못했습니다.\n\n캐시 위치:\n{cache_root}\n\n패처를 닫은 뒤 objects, packages, manifests 폴더를 직접 삭제하시면 됩니다.",

    # Archived snapshot dialogs
    "Select Sierra Archived snapshot": "로컬 저장 스냅샷 선택",
    "Choose where to store the Archived snapshot": "보관 스냅샷을 저장할 위치 선택",
    "Archived snapshot required": "로컬 스냅샷 필요",
    "Select a valid Sierra Archived snapshot folder first.": "올바른 로컬 스냅샷 폴더를 선택해 주세요.",
    "Archived snapshot created successfully.\n\nRelease: {package_id}\nLocation:\n{root}\n\nThe snapshot keeps the package in manifest/object form and reconstructs it only when installed.":
        "보관 스냅샷 생성.\n\n릴리스: {package_id}\n위치:\n{root}\n\n스냅샷은 패키지를 매니페스트/객체 형식으로 보관하고, 설치할 때만 재구성함.",
    "Could not create the Archived snapshot. See Logs for details.":
        "보관 스냅샷을 생성 실패. 자세한 내용은 로그를 확인해 주세요.",

    # Dependency prompt
    ".NET Dependencies": ".NET 필수 구성 요소",
    ".NET Framework 4.7.2 or newer": ".NET Framework 4.7.2 이상",
    ".NET Runtime": ".NET 런타임",
    "ASP.NET Core Runtime": "ASP.NET Core 런타임",
    ".NET Desktop Runtime": ".NET 데스크톱 런타임",
    "{runtime_name} {train} x64": "{runtime_name} {train} x64",
    "Requires {framework} {version} or a newer patch within the {train} runtime train.":
        "{framework} {version} 이상, {train} 런타임 계열의 더 최신 패치 버전이 필요함.",
    "Declared by {sources}.": "{sources}에서 요구하는 구성 요소.",
    "{release} needs additional Microsoft .NET components.":
        "{release}에 Microsoft .NET 구성 요소가 추가로 필요함.",
    "Install these from Microsoft, then press Install again. You can continue if you have already installed them elsewhere.":
        "Microsoft에서 아래 항목을 설치한 뒤 설치 버튼을 다시 눌러 주십시오. 이미 다른 방법으로 설치했다면 계속 진행할 수 있습니다.",
    "Open links": "링크 열기",
    "Copy links": "링크 복사",
    "Continue anyway": "무시하고 계속",
    "this patch": "해당 패치",
    "SPT 4.1 requirement is provisional until stable release docs are available.":
        "SPT 4.1 요구 사항은 안정 버전 문서가 공개되기 전까지 임시 정보임.",

    # Information tab and utility dialogs
    "Patchers": "패처 목록",
    "Docs": "문서",
    "Homepage": "홈페이지",
    "About": "정보",
    "Version: {version}": "버전: {version}",
    "Sierra Installer provides patch generation/application for SPT installations.":
        "Sierra Installer는 SPT 설치용 패치를 만들고 적용하는 도구임.",
    "Support": "지원",
    "mail address:": "이메일 주소:",
    "Copy email": "이메일 복사",
    "Open link": "링크 열기",
    "Failed to open link:\n{error}": "링크를 열지 못함:\n{error}",
    "Copied": "복사 완료",
    "Copied to clipboard.": "클립보드에 복사했음.",

    # Developer generation screen
    "Patch package": "패치 패키지",
    "Source (clean game)": "원본 (본섭 폴더)",
    "Target (SPT)": "대상 (SPT)",
    "Release title": "릴리스 이름",
    "Date": "날짜",
    "Diff aggressiveness": "델타 세밀도",
    "Fast (bigger patches)": "빠르게 (패치 용량 큼)",
    "Balanced": "균형",
    "Aggressive (smallest patches)": "강하게 (패치 용량 최소)",
    "MAX (experimental)": "최대 (실험적)",
    "Integrity check folders": "무결성 검사 폴더",
    "Tracked folders: (none)": "추적 폴더: (없음)",
    "Tracked folders: {folders}": "추적 폴더: {folders}",
    "Source required": "원본 폴더 필요",
    "Select a valid clean Source folder first.": "먼저 올바른 원본 게임 폴더를 선택해 주세요.",
    "Select a valid Source (clean game) folder first.": "먼저 올바른 원본 게임 폴더를 선택해 주세요.",
    "Choose folder inside Source": "원본 폴더 안에서 선택",
    "Choose folder to track (inside Source)": "원본 폴더 안에서 추적할 폴더 선택",
    "Invalid folder": "잘못된 폴더",
    "Choose a folder inside the Source directory.": "원본 폴더 안에 있는 폴더를 선택해 주세요.",
    "Please choose a folder inside the Source directory.": "원본 폴더 안에 있는 폴더를 선택해 주세요.",
    "Add folder...": "폴더 추가...",
    "Delivery": "배포",
    "Standalone": "단독 패키지",
    "Web delivery": "웹 배포",
    "Both": "둘 다",
    "Delivery mode": "배포 방식",
    "Package ID": "패키지 ID",
    "Repository output": "저장소 출력 폴더",
    "Select web repository output": "웹 저장소 출력 폴더 선택",
    "Chunk size (MiB)": "조각 크기 (MiB)",
    "Publishing workers": "동시 게시 수",
    "Web output uses releases/<ID>/manifest.json and a shared objects/ tree. Upload objects first and the manifest last.":
        "웹 출력은 releases/<ID>/manifest.json과 공용 objects/ 구조를 사용함. objects를 먼저 올리고 매니페스트를 마지막에 올리세요.",
    "Generate patch package": "패치 패키지 생성",
    "Generate web release": "웹 릴리스 생성",
    "Missing folders": "폴더 필요",
    "Set both Source and Target folders.": "원본 폴더와 대상 폴더를 모두 지정해 주세요.",
    "Please set Source and Target folders.": "원본 폴더와 대상 폴더를 지정해 주세요.",
    "Package ID required": "패키지 ID 필요",
    "Enter a machine-safe Package ID, such as 3.9.8.": "3.9.8처럼 시스템에서 안전하게 쓸 수 있는 패키지 ID를 입력해 주세요.",
    "Generating patches": "패치 생성 중",
    "Packing additional files": "추가 파일 묶는 중",
    "Building delete list": "삭제 목록 만드는 중",
    "Stamping metadata": "메타데이터 기록 중",
    "Auditing patch package": "패치 패키지 검사 중",
    "Finalizing standalone package": "단독 패키지 마무리 중",
    "Generation completed successfully.": "생성을 완료했어요.",
    "Standalone:\n{path}": "단독 패키지:\n{path}",
    "Web repository:\n{path}": "웹 저장소:\n{path}",
    "Generation failed. See Logs for details.": "생성 실패. 자세한 내용은 로그를 확인해 주세요.",
    "delete list written": "삭제 목록 기록 완료",
    "metadata stamped": "메타데이터 기록 완료",

    # Developer repository screen
    "Repository": "저장소",
    "Local repository": "로컬 저장소",
    "Repository directory": "저장소 폴더",
    "Select Sierra web repository": "Sierra 웹 저장소 선택",
    "Release metadata": "릴리스 메타데이터",
    "Live version": "라이브 버전",
    "Description / date": "설명 / 날짜",
    "Dependencies": "필수 구성 요소",
    "Integrity folders": "무결성 폴더",
    "Clear integrity checks": "무결성 검사 지우기",
    "Save metadata to release": "릴리스에 메타데이터 저장",
    "Repository maintenance": "저장소 관리",
    "Verify selected release": "선택한 릴리스 확인",
    "Rebuild catalog.json from local releases": "로컬 릴리스로 catalog.json 다시 만들기",
    "Select a repository release.": "저장소 릴리스를 선택해 주세요.",
    "choose release": "릴리스 선택",
    "JSON object. Clearing this removes aggregate folder-size checks from the release metadata.":
        "JSON 객체. 내용을 지우면 릴리스 메타데이터의 폴더 전체 크기 검사가 제거됩니다.",
    "These tools operate only on the selected local repository. They never modify the HFS server directly.":
        "이 도구는 선택한 로컬 저장소만 변경함. HFS 서버를 직접 수정하지 않음.",
    "Metadata edits create/reuse a new SHA-256 object and update only storage/metadata.info in the selected manifest. Old objects are left intact.":
        "메타데이터를 수정하면 새 SHA-256 객체를 만들거나 기존 객체를 재사용하고, 선택한 매니페스트의 storage/metadata.info만 갱신함. 이전 객체는 그대로 남음.",
    "Could not inspect repository: {error}": "저장소를 확인하지 못했음: {error}",
    "Local repository contains {count} release(s).": "로컬 저장소에 릴리스 {count}개가 있습니다.",
    "No release manifests found in this local repository.": "이 로컬 저장소에는 릴리스 매니페스트가 없습니다.",
    "Could not load {release} metadata: {error}": "{release} 메타데이터를 불러오지 못했어요: {error}",
    "Loaded metadata for {release}.": "{release} 메타데이터를 불러왔어요.",
    "Integrity checks cleared in the editor. Click Save metadata to publish the change locally.":
        "편집기에서 무결성 검사를 삭제함. 로컬에 반영하려면 메타데이터 저장을 누르세요.",
    "no release metadata is loaded": "불러온 릴리스 메타데이터가 없습니다",
    "integrity folders JSON is invalid: {error}": "무결성 폴더 JSON이 올바르지 않음: {error}",
    "integrity folders must be a JSON object": "무결성 폴더 값은 JSON 객체여야 해함",
    "integrity folder paths must be non-empty strings": "무결성 폴더 경로는 비어 있지 않은 문자열이어야 함",
    "integrity size for {path} must be a non-negative integer": "{path}의 무결성 크기는 0 이상의 정수여야 함",
    "Metadata": "메타데이터",
    "Update local release metadata": "로컬 릴리스 메타데이터 갱신",
    "Update metadata for {release} in the local repository?\n\nThis creates/reuses a new content-addressed object and updates the local manifest. It does not upload anything to HFS.":
        "로컬 저장소의 {release} 메타데이터를 갱신하시겠습니까?\n\n새 콘텐츠 주소 지정 객체를 만들거나 기존 객체를 재사용하고 로컬 매니페스트를 갱신합니다. HFS에는 직접적으로 업로드하지 않습니다.",
    "Repository metadata": "저장소 메타데이터",
    "Updated {release} metadata. New object: {object_id}. Upload that object and the updated release manifest to HFS; catalog.json is unchanged.":
        "{release} 메타데이터를 갱신했음. 새 객체: {object_id}. 이 객체와 갱신된 릴리스 매니페스트를 HFS에 업로드 하십시오. catalog.json은 바뀌지 않았습니다.",
    "Repository catalog": "저장소 카탈로그",
    "Rebuilt {name} with {count} local release(s): {releases}": "로컬 릴리스 {count}개로 {name}을 다시 만들었음: {releases}",
    "(none)": "(없음)",
    "Verifying {release}...": "{release} 확인 중...",
    "Verifying {release}: {current}/{total}  {path}": "{release} 확인 중: {current}/{total}  {path}",
    "Verified {release}: {files} logical file(s), {objects} object reference(s), {size:,.1f} MiB.":
        "{release} 확인 완료: 논리 파일 {files}개, 객체 참조 {objects}개, {size:,.1f} MiB.",
    "Verification failed: {error}": "확인 실패: {error}",
    "Repository verification": "저장소 확인",
}


_TRANSLATIONS = {"ko": _KO}
_language = DEFAULT_LANGUAGE
_FORMATTER = Formatter()


def _translation_variants(source: str) -> tuple[str, ...]:
    variants = [source]
    for messages in _TRANSLATIONS.values():
        translated = messages.get(source)
        if translated is not None and translated not in variants:
            variants.append(translated)
    return tuple(variants)


_SOURCE_MESSAGES = tuple(
    dict.fromkeys(
        source
        for messages in _TRANSLATIONS.values()
        for source in messages
    )
)
_EXACT_SOURCES: dict[str, str] = {}
for _source in _SOURCE_MESSAGES:
    for _variant in _translation_variants(_source):
        _EXACT_SOURCES.setdefault(_variant, _source)


def _compile_template(template: str):
    pieces = ["^"]
    fields: dict[str, str] = {}
    literal_size = 0
    for literal, field_name, _format_spec, _conversion in _FORMATTER.parse(template):
        pieces.append(re.escape(literal))
        literal_size += len(literal)
        if field_name is None:
            continue
        group_name = fields.get(field_name)
        if group_name is None:
            group_name = f"value_{len(fields)}"
            fields[field_name] = group_name
            pieces.append(f"(?P<{group_name}>.*?)")
        else:
            pieces.append(f"(?P={group_name})")
    pieces.append("$")
    return re.compile("".join(pieces), re.DOTALL), fields, literal_size


_TEMPLATE_MATCHERS = []
for _source in _SOURCE_MESSAGES:
    if not any(field_name is not None for _, field_name, _, _ in _FORMATTER.parse(_source)):
        continue
    for _variant in _translation_variants(_source):
        _pattern, _fields, _literal_size = _compile_template(_variant)
        _TEMPLATE_MATCHERS.append(
            (_literal_size, _source, _pattern, tuple(_fields.items()))
        )
_TEMPLATE_MATCHERS.sort(key=lambda item: item[0], reverse=True)


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().replace("-", "_").lower().split(".", 1)[0]
    windows_names = {
        "english": "en",
        "korean": "ko",
    }
    for name, code in windows_names.items():
        if normalized == name or normalized.startswith(name + "_"):
            return code
    if normalized in SUPPORTED_LANGUAGES:
        return normalized
    prefix = normalized.split("_", 1)[0]
    return prefix if prefix in SUPPORTED_LANGUAGES else None


def settings_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "SierraPatcher" / "settings.json"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "sierra-patcher" / "settings.json"


def _saved_language() -> str | None:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return normalize_language(data.get("language"))


def _system_language() -> str:
    """Use Korean only for a Korean system locale; otherwise use English."""
    try:
        system_locale = locale.getlocale()[0]
    except (locale.Error, TypeError, ValueError):
        system_locale = None
    return "ko" if normalize_language(system_locale) == "ko" else DEFAULT_LANGUAGE


def detect_language() -> str:
    return (
        normalize_language(os.environ.get(LANGUAGE_ENV))
        or _saved_language()
        or _system_language()
    )


def current_language() -> str:
    return _language


def alternate_language(language: str | None = None) -> str:
    active = current_language() if language is None else normalize_language(language)
    if active not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language}")
    return next(
        (code for code in SUPPORTED_LANGUAGES if code != active),
        active,
    )


def set_language(language: str, *, persist: bool = False) -> str:
    normalized = normalize_language(language)
    if normalized is None:
        raise ValueError(f"unsupported language: {language}")

    if persist:
        _save_language(normalized)

    global _language
    _language = normalized
    return normalized


def _save_language(language: str) -> None:
    path = settings_path()
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current["language"] = language

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def tr(message: str, **values) -> str:
    template = _TRANSLATIONS.get(_language, {}).get(message, message)
    return template.format(**values) if values else template


def _render_captured(template: str, values: dict[str, str]) -> str:
    rendered = []
    for literal, field_name, _format_spec, _conversion in _FORMATTER.parse(template):
        rendered.append(literal)
        if field_name is not None:
            rendered.append(values.get(field_name, ""))
    return "".join(rendered)


@lru_cache(maxsize=4096)
def _retranslate(language: str, message: str) -> str:
    source = _EXACT_SOURCES.get(message)
    if source is not None:
        return _TRANSLATIONS.get(language, {}).get(source, source)

    for _literal_size, source, pattern, fields in _TEMPLATE_MATCHERS:
        match = pattern.fullmatch(message)
        if match is None:
            continue
        values = {
            field_name: match.group(group_name)
            for field_name, group_name in fields
        }
        target = _TRANSLATIONS.get(language, {}).get(source, source)
        return _render_captured(target, values)

    return _translate_progress_text(message, language)


def retranslate(message: str) -> str:
    """Translate already-rendered UI text without changing unknown user data."""
    return _retranslate(current_language(), str(message))


def retranslate_exact(message: str) -> str:
    """Retranslate only an exact registered UI string, never formatted user data."""
    text = str(message)
    source = _EXACT_SOURCES.get(text)
    if source is None:
        return text
    return _TRANSLATIONS.get(current_language(), {}).get(source, source)


def canonical_text(message: str) -> str:
    """Return the English source rendering for known UI text."""
    return _retranslate(DEFAULT_LANGUAGE, str(message))


def canonical_exact_text(message: str) -> str:
    """Return the English source for an exact registered UI string."""
    text = str(message)
    return _EXACT_SOURCES.get(text, text)


_PROGRESS_EXACT = {
    "Validating package...": "패키지를 확인하는 중...",
    "Validating resources...": "필요한 리소스를 확인하는 중...",
    "Removing downloaded patch data...": "다운로드한 패치 데이터를 삭제하는 중...",
    "Downloading manifest": "매니페스트 다운로드 중",
    "Downloading manifest for Archived snapshot": "보관 스냅샷 매니페스트 다운로드 중",
    "Manifest ready": "매니페스트 준비 완료",
    "No objects required": "필요한 객체 없음",
    "No package files": "패키지 파일 없음",
    "No archived objects to verify": "확인할 보관 객체 없음",
    "No delta patches generated": "생성된 델타 패치 없음",
    "No patches to verify": "확인할 패치 없음",
    "delete list written": "삭제 목록 기록 완료",
    "metadata stamped": "메타데이터 기록 완료",
    "cleanup done": "정리 완료",
    "Cancelled": "취소됨",
}
_PROGRESS_STATES = {
    "cached": "캐시 사용",
    "downloaded": "다운로드 완료",
    "ready": "준비 완료",
}
_PROGRESS_EXACT_REVERSE = {value: key for key, value in _PROGRESS_EXACT.items()}
_PROGRESS_STATE_REVERSE = {value: key for key, value in _PROGRESS_STATES.items()}


def _translate_progress_text(message: str, language: str) -> str:
    text = str(message)
    if language == "ko":
        if text in _PROGRESS_EXACT_REVERSE or text in _PROGRESS_STATE_REVERSE:
            return text
        if text in _PROGRESS_EXACT:
            return _PROGRESS_EXACT[text]
        if text in _PROGRESS_STATES:
            return _PROGRESS_STATES[text]

        match = re.fullmatch(r"(\d+)/(\d+) objects(?:\s+(.*))?", text)
        if match:
            completed, total, detail = match.groups()
            suffix = f" {_PROGRESS_STATES.get(detail, detail)}" if detail else ""
            return f"객체 {completed}/{total}{suffix}"

        match = re.fullmatch(r"(cached|ready): (.+)", text)
        if match:
            state, path = match.groups()
            return f"{_PROGRESS_STATES[state]}: {path}"

        patterns = (
            (r"verified ([0-9a-fA-F]+)", lambda m: f"확인 완료 {m.group(1)}"),
            (r"discarded ([0-9a-fA-F]+)", lambda m: f"폐기됨 {m.group(1)}"),
            (r"verifying (\d+)/(\d+) objects", lambda m: f"객체 확인 중 {m.group(1)}/{m.group(2)}"),
            (r"verified (\d+)/(\d+) objects \(([^)]+)\)", lambda m: f"객체 확인 완료 {m.group(1)}/{m.group(2)} ({m.group(3)})"),
            (r"processed (\d+)/(\d+) \(([^)]+)\)", lambda m: f"처리 완료 {m.group(1)}/{m.group(2)} ({m.group(3)})"),
            (r"compressed (\d+)/(\d+)", lambda m: f"압축 완료 {m.group(1)}/{m.group(2)}"),
            (r"applied (.+)", lambda m: f"적용 완료 {m.group(1)}"),
            (r"patched (\d+)/(\d+)", lambda m: f"패치 생성 완료 {m.group(1)}/{m.group(2)}"),
            (r"Audited (\d+)/(\d+)", lambda m: f"검사 완료 {m.group(1)}/{m.group(2)}"),
            (r"Validating patches (\d+)/(\d+)", lambda m: f"패치 확인 중 {m.group(1)}/{m.group(2)}"),
            (r"retry (\d+)/(\d+) (\d+)/(\d+)", lambda m: f"재시도 {m.group(1)}/{m.group(2)} 진행 {m.group(3)}/{m.group(4)}"),
            (r"hashed (\d+)/(\d+) delta sources", lambda m: f"델타 원본 해시 생성 {m.group(1)}/{m.group(2)}"),
            (r"verified (\d+)/(\d+) source files", lambda m: f"원본 파일 확인 완료 {m.group(1)}/{m.group(2)}"),
        )
    else:
        if text in _PROGRESS_EXACT or text in _PROGRESS_STATES:
            return text
        if text in _PROGRESS_EXACT_REVERSE:
            return _PROGRESS_EXACT_REVERSE[text]
        if text in _PROGRESS_STATE_REVERSE:
            return _PROGRESS_STATE_REVERSE[text]

        match = re.fullmatch(r"객체 (\d+)/(\d+)(?:\s+(.*))?", text)
        if match:
            completed, total, detail = match.groups()
            suffix = f" {_PROGRESS_STATE_REVERSE.get(detail, detail)}" if detail else ""
            return f"{completed}/{total} objects{suffix}"

        reverse_states = "|".join(re.escape(value) for value in _PROGRESS_STATES.values())
        match = re.fullmatch(rf"({reverse_states}): (.+)", text)
        if match:
            state, path = match.groups()
            return f"{_PROGRESS_STATE_REVERSE[state]}: {path}"

        patterns = (
            (r"확인 완료 ([0-9a-fA-F]+)", lambda m: f"verified {m.group(1)}"),
            (r"폐기됨 ([0-9a-fA-F]+)", lambda m: f"discarded {m.group(1)}"),
            (r"객체 확인 중 (\d+)/(\d+)", lambda m: f"verifying {m.group(1)}/{m.group(2)} objects"),
            (r"객체 확인 완료 (\d+)/(\d+) \(([^)]+)\)", lambda m: f"verified {m.group(1)}/{m.group(2)} objects ({m.group(3)})"),
            (r"처리 완료 (\d+)/(\d+) \(([^)]+)\)", lambda m: f"processed {m.group(1)}/{m.group(2)} ({m.group(3)})"),
            (r"압축 완료 (\d+)/(\d+)", lambda m: f"compressed {m.group(1)}/{m.group(2)}"),
            (r"적용 완료 (.+)", lambda m: f"applied {m.group(1)}"),
            (r"패치 생성 완료 (\d+)/(\d+)", lambda m: f"patched {m.group(1)}/{m.group(2)}"),
            (r"검사 완료 (\d+)/(\d+)", lambda m: f"Audited {m.group(1)}/{m.group(2)}"),
            (r"패치 확인 중 (\d+)/(\d+)", lambda m: f"Validating patches {m.group(1)}/{m.group(2)}"),
            (r"재시도 (\d+)/(\d+) 진행 (\d+)/(\d+)", lambda m: f"retry {m.group(1)}/{m.group(2)} {m.group(3)}/{m.group(4)}"),
            (r"델타 원본 해시 생성 (\d+)/(\d+)", lambda m: f"hashed {m.group(1)}/{m.group(2)} delta sources"),
            (r"원본 파일 확인 완료 (\d+)/(\d+)", lambda m: f"verified {m.group(1)}/{m.group(2)} source files"),
        )

    for pattern, render in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return render(match)
    return text


def tr_progress(message: str) -> str:
    return _translate_progress_text(str(message), current_language())


def localized_choices(choices: Iterable[str]) -> tuple[str, ...]:
    return tuple(tr(choice) for choice in choices)


def canonical_choice(display_value: str, choices: Iterable[str]) -> str:
    value = str(display_value)
    for choice in choices:
        variants = {choice}
        variants.update(
            messages.get(choice, choice)
            for messages in _TRANSLATIONS.values()
        )
        if value in variants:
            return choice
    return value


set_language(detect_language())
