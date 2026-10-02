from negahban.graph import flatten_comments

TS = "2026-10-02T09:00:00+0000"


def _item(cid: str, text: str, replies: list[dict] | None = None, user: str = "u") -> dict:
    item = {"id": cid, "text": text, "timestamp": TS, "from": {"id": "1", "username": user}}
    if replies is not None:
        item["replies"] = {"data": replies}
    return item


def test_replies_listed_twice_by_instagram_are_kept_once() -> None:
    reply = _item("2", "reply", user="replier")
    items = [
        _item("1", "parent", replies=[reply]),
        reply,  # Instagram repeats every reply as a top-level item
        _item("3", "another"),
    ]
    comments = flatten_comments(items, "m")
    assert [c.comment_id for c in comments] == ["1", "2", "3"]
    assert comments[1].parent_id == "1"
    assert comments[1].username == "replier"
    assert comments[0].parent_id is None


def test_reply_seen_top_level_before_its_parent_still_gets_the_parent() -> None:
    reply = _item("2", "reply")
    comments = flatten_comments([reply, _item("1", "parent", replies=[reply])], "m")
    assert {(c.comment_id, c.parent_id) for c in comments} == {("1", None), ("2", "1")}
    assert len(comments) == 2


def test_owner_comment_falls_back_to_username_field() -> None:
    item = {"id": "9", "text": "hi", "timestamp": TS, "username": "owner"}
    assert flatten_comments([item], "m")[0].username == "owner"
