import tempfile
import unittest
from pathlib import Path

from curldesk.models import RequestData, ResponseData
from curldesk.storage import Storage


class StorageTests(unittest.TestCase):
    def test_collection_and_response_history_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "data.sqlite3")
            request = RequestData(
                method="POST",
                url="https://example.test",
                auth_type="Bearer Token",
                bearer_token="sensitive-token",
            )
            storage.save_collection("create", request)
            loaded = storage.get_collection("create")
            self.assertEqual(loaded, request)

            response = ResponseData(
                status_code=201,
                reason="Created",
                elapsed_ms=12.5,
                headers=[("Content-Type", "application/json")],
                body=b'{"ok":true}',
                final_url="https://example.test",
            )
            history_id = storage.add_history(request, response)
            history_request = storage.get_history_request(history_id)
            history_response = storage.get_history_response(history_id)
            self.assertEqual(history_request, request)
            self.assertEqual(history_response.status_code, 201)
            self.assertEqual(history_response.body, b'{"ok":true}')

            storage.delete_collection("create")
            self.assertEqual(storage.list_collections(), [])
            self.assertEqual(len(storage.list_history()), 1)


if __name__ == "__main__":
    unittest.main()
