from vision_app.chat.commands import parse_command


def test_parse_fix_teach():
    assert parse_command("/fix It should have checked the weather").name == "fix"
    assert parse_command("/teach Paris is the capital of Paris").argument.startswith("Paris")
    assert parse_command("hello") is None
