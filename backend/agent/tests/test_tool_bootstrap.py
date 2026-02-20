from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.tool_bootstrap import ensure_monkey_aee_tool
from backend.models.schemas import Base, Tool, ToolCategory


def test_ensure_monkey_aee_tool_idempotent():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    first_tool, first_created = ensure_monkey_aee_tool(session)
    second_tool, second_created = ensure_monkey_aee_tool(session)

    assert first_created is True
    assert second_created is False
    assert first_tool.id == second_tool.id
    assert second_tool.script_class == "MonkeyAEEStabilityTest"
    assert second_tool.script_path.endswith("backend\\agent\\tools\\monkey_aee_stability_test.py") or second_tool.script_path.endswith("backend/agent/tools/monkey_aee_stability_test.py")

    assert session.query(ToolCategory).filter(ToolCategory.name == "Monkey").count() == 1
    assert session.query(Tool).filter(Tool.name == "MONKEY_AEE Stability").count() == 1

    session.close()

