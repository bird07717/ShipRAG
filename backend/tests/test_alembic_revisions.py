from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_all_alembic_revisions_can_be_loaded() -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[1] / "alembic"),
    )

    script = ScriptDirectory.from_config(config)

    assert script.get_heads()
    assert list(script.walk_revisions())
