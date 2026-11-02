# -*- coding: utf-8 -*-
"""消息记录（message_records / agent_messages）自测脚本（离线，不联网）

覆盖场景：
1. 拼多多推送角色判定：from.role=mall_cs -> MALL_CS；客户消息 -> TEXT
2. 客户消息落库：direction=in / msg_type=text
3. 店家人工客服消息落库：direction=out / msg_type=mall_cs，并同步进 AI 会话历史
   （role=assistant, source=mall_cs, session_id=pinduoduo<客户UID>）
4. AI 自动回复落库：direction=out / msg_type=text，会话历史 source=ai
5. 同一条推送（同 msg_id）重复到达：只记 1 行，会话历史不重复
6. 程序自己发出的消息被平台回推（msg_id 不同、内容相同、120s 内）：只记 1 行
7. 混合部署负例：DatabaseManager 缺少 save_message（旧版 database/ + 新版 Message/）
   -> 必须"明确报错 + 降级写入成功"，绝不静默丢数据
8. 控制命令 STOP AI / START AI：只写 mall_cs_control 审计行，不写入 AI 会话历史
9. 人工客服消息缺少客户 UID：不抛异常、不落库、有 WARNING

运行：PYTHONIOENCODING=utf-8 python scripts/test_message_records.py
"""
import logging
import os
import shutil
import sys
import tempfile
import types

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 自测的临时数据库统一放到 temp/ 下，免得在项目根堆一堆 ship_guard_xxx 目录
tempfile.tempdir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)


def _install_stubs():
    """本地/打包环境常缺 websockets、openai、jieba；用桩模块/桩包绕过，保证自测可离线运行"""
    try:
        import websockets  # noqa: F401
    except Exception:
        ws = types.ModuleType("websockets")
        exc = types.ModuleType("websockets.exceptions")

        class WebSocketException(Exception):
            pass

        exc.WebSocketException = WebSocketException
        ws.exceptions = exc
        sys.modules["websockets"] = ws
        sys.modules["websockets.exceptions"] = exc

    # Agent.CustomerAgent.custom.__init__ 会 import openai / jieba，这里用桩包跳过其 __init__
    for name, rel in (
        ("Agent", "Agent"),
        ("Agent.CustomerAgent", os.path.join("Agent", "CustomerAgent")),
        ("Agent.CustomerAgent.custom", os.path.join("Agent", "CustomerAgent", "custom")),
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = [os.path.join(ROOT, rel)]
            sys.modules[name] = mod


_install_stubs()

from sqlalchemy import text as _sql_text

from database.db_manager import DatabaseManager
from core.di_container import container
from database.models import MessageRecord
from bridge.context import Context, ContextType, ChannelType
from Channel.pinduoduo.pdd_message import PDDChatMessage
from Channel.pinduoduo.core.pdd_message_handler import MessageHandlerMixin
import Message.core.persistence as persistence
from Agent.CustomerAgent.custom.agent_config import AgentConfig
from Agent.CustomerAgent.custom.session_manager import SessionManager

SHOP_ID = "SHOP1"
USER_ID = "ACC1"
CUSTOMER = "4111009570"
CS_UID = "13676048541"

_results = {"pass": 0, "fail": 0}
_FAKE_SELF = types.SimpleNamespace(
    channel_name="pinduoduo", logger=logging.getLogger("test_msg_records"))

_db = None
_db_path = None
_tmpdir = None


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, detail))


def query(sql, params=None):
    with _db.engine.connect() as conn:
        return [tuple(r) for r in conn.execute(_sql_text(sql), params or {})]


def make_context(content, ctype=ContextType.TEXT, msg_id="m1", from_uid=CUSTOMER,
                 to_uid=CS_UID, shop_id=SHOP_ID, user_id=USER_ID):
    return Context.create_pinduoduo_context(
        content=content,
        msg_id=msg_id,
        from_uid=from_uid,
        to_uid=to_uid,
        nickname="客户昵称",
        user_msg_type=ctype,
        shop_id=shop_id,
        user_id=user_id,
        username="acc",
        channel_type=ChannelType.PINDUODUO,
    )


def mall_cs_context(content, msg_id="cs-1", from_uid=CS_UID, to_uid=CUSTOMER):
    """按真实链路构造店家(mall_cs)消息的 Context：
    PDD 推送里 mall_cs 的 from 是本店客服账号，to 才是客户 —— 必须走真实转换函数，
    否则 from/to 会反，归属客户就会错。
    """
    payload = push_payload("mall_cs", from_uid, "user", to_uid, content, msg_id=msg_id)
    msg = PDDChatMessage(payload)
    return MessageHandlerMixin._convert_to_context(_FAKE_SELF, msg, SHOP_ID, USER_ID, "acc")


def push_payload(from_role, from_uid, to_role, to_uid, content, msg_id="m1"):
    return {
        "response": "push",
        "message": {
            "msg_id": msg_id,
            "nickname": "客户昵称",
            "type": 0,
            "content": content,
            "from": {"role": from_role, "uid": from_uid},
            "to": {"role": to_role, "uid": to_uid},
            "time": 1758600000000,
        },
    }


def setup():
    global _db, _db_path, _tmpdir
    _tmpdir = tempfile.mkdtemp(prefix="msg_records_")
    _db_path = os.path.join(_tmpdir, "test.db")
    _db = DatabaseManager(db_path=_db_path)
    container.register_singleton(DatabaseManager, instance=_db)
    # 让 agent_messages 也写进临时库，避免污染真实数据库
    AgentConfig.load_from_config = classmethod(lambda cls: AgentConfig(db_path=_db_path))
    # 真实转换函数需要店铺信息，测试里直接给个假的
    from database import db_manager as _dbm
    _dbm.get_shop = lambda *a, **k: {"shop_name": "测试店"}


def s1_role_gate():
    print("\n[场景 1] 拼多多推送角色判定（mall_cs vs 客户）")
    cs = PDDChatMessage(push_payload("mall_cs", CS_UID, "user", CUSTOMER, "亲，人工帮您处理"))
    cu = PDDChatMessage(push_payload("user", CUSTOMER, "mall_cs", CS_UID, "帮我查序列号"))
    check("店家消息判定为 MALL_CS", cs.user_msg_type == ContextType.MALL_CS,
          "实际=%s" % cs.user_msg_type)
    check("客户消息判定为 TEXT", cu.user_msg_type == ContextType.TEXT,
          "实际=%s" % cu.user_msg_type)
    check("店家消息 from=客服账号 / to=客户", cs.from_uid == CS_UID and cs.to_uid == CUSTOMER,
          "from=%s to=%s" % (cs.from_uid, cs.to_uid))


def s2_incoming():
    print("\n[场景 2] 客户消息落库")
    ctx = make_context("帮我查一下序列号", msg_id="cust-1")
    ok = persistence.record_incoming_message(ctx)
    rows = query("SELECT direction, msg_type, from_uid, account_id, content "
                 "FROM message_records WHERE msg_id='cust-1'")
    check("写入成功返回 True", ok is True)
    check("记录为 in/text", len(rows) == 1 and rows[0][0] == "in" and rows[0][1] == "text",
          str(rows))
    check("归属客户 UID 正确", len(rows) == 1 and rows[0][2] == CUSTOMER, str(rows))


def s3_mall_cs():
    print("\n[场景 3] 店家人工客服消息落库 + 同步 AI 会话历史")
    ctx = mall_cs_context("亲，稍等我人工帮您处理", msg_id="cs-1")
    print("      归属客户 = %s（客服账号 = %s）" % (ctx.kwargs.to_uid, ctx.kwargs.from_uid))
    MessageHandlerMixin._record_mall_cs_message(_FAKE_SELF, ctx)

    rows = query("SELECT direction, msg_type, from_uid, account_id, content "
                 "FROM message_records WHERE msg_id='cs-1'")
    check("主表记录为 out/mall_cs", len(rows) == 1 and rows[0][0] == "out" and rows[0][1] == "mall_cs",
          str(rows))
    check("主表归属客户 UID（不是客服账号）", len(rows) == 1 and rows[0][2] == CUSTOMER, str(rows))

    sess = query("SELECT session_id, role, source, content FROM agent_messages "
                 "WHERE content='亲，稍等我人工帮您处理'")
    check("AI 会话历史 session_id=pinduoduo<客户UID>",
          len(sess) == 1 and sess[0][0] == "pinduoduo" + CUSTOMER, str(sess))
    check("AI 会话历史 role=assistant + source=mall_cs",
          len(sess) == 1 and sess[0][1] == "assistant" and sess[0][2] == "mall_cs", str(sess))
    cols = query("PRAGMA table_info(agent_messages)")
    check("agent_messages 已具备 source 列", any(c[1] == "source" for c in cols),
          str([c[1] for c in cols]))


def s4_ai_reply():
    print("\n[场景 4] AI 自动回复落库")
    ctx = make_context("你好", msg_id="cust-2")
    persistence.record_outgoing_message(ctx, "您好，请问有什么可以帮您？", msg_type="text")
    # CustomerAgent 写会话历史的等价调用
    persistence.record_agent_session_message(
        channel="pinduoduo", customer_uid=CUSTOMER, role="assistant",
        content="您好，请问有什么可以帮您？", source="ai")
    rows = query("SELECT direction, msg_type FROM message_records "
                 "WHERE content='您好，请问有什么可以帮您？'")
    check("主表记录为 out/text", rows == [("out", "text")], str(rows))
    sess = query("SELECT role, source FROM agent_messages WHERE content='您好，请问有什么可以帮您？'")
    check("会话历史 source=ai", sess == [("assistant", "ai")], str(sess))


def s5_duplicate_push():
    print("\n[场景 5] 同一条推送（同 msg_id）重复到达")
    ctx = mall_cs_context("重复推送的人工回复", msg_id="cs-dup")
    MessageHandlerMixin._record_mall_cs_message(_FAKE_SELF, ctx)
    MessageHandlerMixin._record_mall_cs_message(_FAKE_SELF, ctx)
    rows = query("SELECT COUNT(*) FROM message_records WHERE msg_id='cs-dup'")
    sess = query("SELECT COUNT(*) FROM agent_messages WHERE content='重复推送的人工回复'")
    check("主表只记 1 行", rows[0][0] == 1, "实际=%s" % rows[0][0])
    check("会话历史只记 1 条", sess[0][0] == 1, "实际=%s" % sess[0][0])


def s6_echo_self_sent():
    print("\n[场景 6] 程序自己发出的消息被平台回推（msg_id 不同、内容相同）")
    ctx = make_context("本店自动回复内容", msg_id="cust-3")
    persistence.record_outgoing_message(ctx, "本店自动回复内容", msg_type="text")
    # 平台把这条回推成 mall_cs 消息
    echo = mall_cs_context("本店自动回复内容", msg_id="cs-echo")
    MessageHandlerMixin._record_mall_cs_message(_FAKE_SELF, echo)
    rows = query("SELECT direction, msg_type FROM message_records WHERE content='本店自动回复内容'")
    check("主表只记 1 行（out/text，回推被去重）", rows == [("out", "text")], str(rows))


def s7_mixed_deployment():
    print("\n[场景 7] 混合部署负例：DatabaseManager 缺少 save_message")
    original = DatabaseManager.save_message

    # check_deployment 在健康状态下应无问题
    check("健康状态 check_deployment() 无问题", persistence.check_deployment() == [],
          str(persistence.check_deployment()))

    try:
        del DatabaseManager.save_message
        persistence._db_capability(force=True)
        persistence._mismatch_reported = False
        persistence._fallback_reported = False

        problems = persistence.check_deployment()
        check("缺 save_message 时自检能报出问题", any("save_message" in p for p in problems),
              str(problems))

        ok = persistence.save_message_record(
            channel="pinduoduo", shop_id=SHOP_ID, account_id=USER_ID, from_uid=CUSTOMER,
            msg_id="mixed-1", direction="out", msg_type="mall_cs", content="混合部署降级写入")
        rows = query("SELECT content FROM message_records WHERE content='混合部署降级写入'")
        check("不再静默：已给出明确 ERROR 标记", persistence._mismatch_reported is True)
        check("降级写入成功（数据没丢）", ok is True and len(rows) == 1, "ok=%s rows=%s" % (ok, rows))
    finally:
        DatabaseManager.save_message = original
        persistence._db_capability(force=True)
        persistence._mismatch_reported = False
        persistence._fallback_reported = False

    check("恢复后 check_deployment() 重新无问题", persistence.check_deployment() == [],
          str(persistence.check_deployment()))


def s8_control_command():
    print("\n[场景 8] 控制命令 STOP AI / START AI")
    ctx = mall_cs_context("STOP AI", msg_id="cs-stop")
    handled = MessageHandlerMixin._try_handle_ai_control_command(_FAKE_SELF, ctx)
    rows = query("SELECT msg_type FROM message_records WHERE content='STOP AI'")
    sess = query("SELECT COUNT(*) FROM agent_messages WHERE content='STOP AI'")
    check("控制命令被识别处理", handled is True)
    check("主表记录为 mall_cs_control（仅审计）", rows == [("mall_cs_control",)], str(rows))
    check("控制命令不写入 AI 会话历史", sess[0][0] == 0, "实际=%s" % sess[0][0])


def s9_missing_uid():
    print("\n[场景 9] 人工客服消息缺少客户 UID")
    ctx = make_context("没有归属客户的消息", ContextType.MALL_CS, msg_id="cs-nouid",
                       from_uid="", to_uid="")
    before = query("SELECT COUNT(*) FROM message_records")[0][0]
    try:
        MessageHandlerMixin._record_mall_cs_message(_FAKE_SELF, ctx)
        raised = False
    except Exception as e:  # noqa: BLE001
        raised = True
        print("      异常:", e)
    after = query("SELECT COUNT(*) FROM message_records")[0][0]
    check("不抛异常", raised is False)
    check("不产生脏记录", before == after, "before=%s after=%s" % (before, after))


def main():
    setup()
    try:
        s1_role_gate()
        s2_incoming()
        s3_mall_cs()
        s4_ai_reply()
        s5_duplicate_push()
        s6_echo_self_sent()
        s7_mixed_deployment()
        s8_control_command()
        s9_missing_uid()
    finally:
        try:
            _db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(_tmpdir, ignore_errors=True)

    print("\n" + "=" * 60)
    print("RESULT: pass=%d fail=%d" % (_results["pass"], _results["fail"]))
    print("=" * 60)
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
