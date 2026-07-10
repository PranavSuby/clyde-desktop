"""Skill loader: discovers user-defined deterministic tools in the skills/ dir.

A skill is a single .py file that defines:

    SKILL = {
        "name": "calculator",
        "description": "shown to the model — say WHEN to use it",
        "parameters": { ... JSON schema, OpenAI function format ... },
    }

    def run(args: dict) -> str:
        ...

Skills run in-process and must be deterministic and fast. See SKILLS.md.
"""

import asyncio
import importlib.util
import multiprocessing
import os
import traceback

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")

DEFAULT_TIMEOUT = 5.0


def _load_module(path: str):
    name = "clydesk_skill_" + os.path.basename(path)[:-3]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _child_run(path: str, args: dict, queue):
    try:
        mod = _load_module(path)
        queue.put(str(mod.run(args)))
    except Exception as e:
        queue.put(f"Error: {type(e).__name__}: {e}")


class Skill:
    def __init__(self, name, description, parameters, fn, path,
                 timeout=DEFAULT_TIMEOUT, in_process=False):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn
        self.path = path
        self.timeout = timeout
        # in_process skills run on a worker thread in this process (needed
        # for skills that hold state or GPU models); no hard-kill on timeout.
        self.in_process = in_process

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, args: dict) -> str:
        """Synchronous, in-process execution (tests, trusted callers)."""
        try:
            return str(self.fn(args))
        except Exception as e:
            return f"Error in skill '{self.name}': {type(e).__name__}: {e}"

    async def run_async(self, args: dict) -> str:
        """Run without blocking the event loop.

        Default: a forked subprocess with a hard kill on timeout, so a
        runaway computation can't freeze or occupy the app. in_process
        skills fall back to a worker thread (soft timeout)."""
        if self.in_process:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self.run, args), self.timeout)
            except asyncio.TimeoutError:
                return f"Error: skill '{self.name}' timed out after {self.timeout}s"
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        proc = ctx.Process(target=_child_run, args=(self.path, args, queue),
                           daemon=True)
        proc.start()
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(queue.get, True, self.timeout + 1),
                self.timeout,
            )
        except (asyncio.TimeoutError, Exception) as e:
            if isinstance(e, asyncio.TimeoutError) or queue.empty():
                return f"Error: skill '{self.name}' timed out after {self.timeout}s"
            return f"Error: skill '{self.name}': {e}"
        finally:
            if proc.is_alive():
                proc.kill()
            proc.join(timeout=1)


def load_skills() -> tuple[dict[str, Skill], list[str]]:
    """(Re)load all skills. Returns ({name: Skill}, [error strings])."""
    skills: dict[str, Skill] = {}
    errors: list[str] = []
    if not os.path.isdir(SKILLS_DIR):
        return skills, errors
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(SKILLS_DIR, fname)
        try:
            mod = _load_module(path)
            meta = getattr(mod, "SKILL", None)
            fn = getattr(mod, "run", None)
            if not isinstance(meta, dict) or not callable(fn):
                errors.append(f"{fname}: needs a SKILL dict and a run(args) function")
                continue
            name = meta.get("name") or fname[:-3]
            if name in skills:
                errors.append(f"{fname}: duplicate skill name '{name}' "
                              f"(also in {os.path.basename(skills[name].path)})")
                continue
            skills[name] = Skill(
                name=name,
                description=meta.get("description", ""),
                parameters=meta.get("parameters", {"type": "object", "properties": {}}),
                fn=fn,
                path=path,
                timeout=float(meta.get("timeout") or DEFAULT_TIMEOUT),
                in_process=bool(meta.get("in_process")),
            )
        except Exception:
            errors.append(f"{fname}: {traceback.format_exc(limit=1).splitlines()[-1]}")
    return skills, errors
