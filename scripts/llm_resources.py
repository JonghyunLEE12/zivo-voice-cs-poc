"""Live validation guard. Coordinator override: swap growth, not residual swap."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
from time import monotonic


class ResourceLimit(RuntimeError):
    pass


class ResourceGuard:
    def __init__(self, log_path='reports/t6_1/resources.jsonl'):
        self.log_path=Path(log_path)
        self.baseline_swap=None
        self.warning_since=None

    def check(self, starting=False):
        memory=subprocess.check_output(['memory_pressure'],text=True)
        swap=subprocess.check_output(['sysctl','vm.swapusage'],text=True)
        pressure=int(subprocess.check_output(['sysctl','-n','kern.memorystatus_vm_pressure_level'],text=True).strip())
        if pressure == 2:
            if self.warning_since is None: self.warning_since=monotonic()
        else:
            self.warning_since=None
        warning_seconds=monotonic()-self.warning_since if self.warning_since is not None else 0
        free=int(re.search(r'free percentage: (\d+)%',memory)[1])
        used=float(re.search(r'used = ([\d.]+)M',swap)[1])
        disk=shutil.disk_usage(Path(__file__).resolve().parents[1]).free/(1024**3)
        if self.baseline_swap is None: self.baseline_swap=used
        sample={'at':datetime.now().isoformat(),'memory_free_percent':free,'swap_used_mib':used,
                'swap_growth_mib':used-self.baseline_swap,'disk_free_gib':disk,'starting':starting,'pressure_level':pressure,'warning_seconds':warning_seconds}
        self.log_path.parent.mkdir(exist_ok=True,parents=True)
        with self.log_path.open('a') as f: f.write(json.dumps(sample)+'\n')
        if pressure == 4 or used-self.baseline_swap > 4096 or disk < 10:
            raise ResourceLimit(f'Resource threshold: {sample}')
        return sample

    async def run(self, factory):
        self.check(starting=True)
        task=asyncio.create_task(factory())
        try:
            while not task.done():
                done,_=await asyncio.wait([task],timeout=5)
                self.check()
            return await task
        finally:
            if not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
