import shutil, tempfile, psutil


def check_resources(min_ram_gb=4, min_temp_gb=10) -> None:
mem = psutil.virtual_memory().available / (1024**3)
tmp = shutil.disk_usage(tempfile.gettempdir()).free / (1024**3)
if mem < min_ram_gb:
print(f"WARNING: Low memory ({mem:.1f} GB available)")
if tmp < min_temp_gb:
print(f"WARNING: Low temp space ({tmp:.1f} GB free)")


def optimal_threads(cap: int = 8) -> int:
# conservative: 2GB per thread and leave one core
cores = max(psutil.cpu_count(logical=False) or 1, 1)
ram_gb = psutil.virtual_memory().total / (1024**3)
by_ram = max(1, int(ram_gb / 2))
by_cpu = max(1, cores - 1)
return max(1, min(by_ram, by_cpu, cap))
