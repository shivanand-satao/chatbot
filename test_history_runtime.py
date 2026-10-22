import unittest
from unittest.mock import MagicMock

from history_runtime import ROLE, store_factory, verify_role
from conversation_context import Owner
from conversation_store import ConversationStore
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus


class RuntimeTests(unittest.TestCase):
    def test_privileged_identity_rejected(self):
        conn = MagicMock()
        row = dict(name=ROLE, rolsuper=False, rolcreaterole=False,
                   rolcreatedb=False, rolbypassrls=False, rolreplication=False)
        for key in ('rolsuper', 'rolcreaterole', 'rolcreatedb', 'rolbypassrls', 'rolreplication'):
            conn.execute.return_value.fetchone.return_value = {**row, key: True}
            with self.assertRaises(RuntimeError):
                verify_role(conn)
        conn.execute.return_value.fetchone.return_value = {**row, 'name': 'postgres'}
        with self.assertRaises(RuntimeError):
            verify_role(conn)

    def test_no_fallback_credential(self):
        for dsn in (None, ''):
            with self.assertRaises(ValueError):
                store_factory(dsn)

    def test_owner_scope_is_transaction_local(self):
        conn = MagicMock()
        conn.row_factory = dict_row
        conn.autocommit = True
        conn.info.transaction_status = TransactionStatus.IDLE
        store = ConversationStore(conn, Owner('user', 7), database_owner_scope=True)
        with store.transaction():
            pass
        statement, params = conn.execute.call_args.args
        self.assertIn('set_config', statement)
        self.assertIn(',true)', statement)
        self.assertEqual(params, ('user', '7'))


if __name__ == '__main__':
    unittest.main()