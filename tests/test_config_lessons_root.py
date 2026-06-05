from app.config import Settings


def test_notion_lessons_root_default():
    s = Settings(_env_file=None)
    assert s.notion_lessons_root == "2c1998381c768063bc43c84d59c0abf3"
