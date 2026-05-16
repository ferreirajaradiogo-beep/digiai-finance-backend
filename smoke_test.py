from fastapi.testclient import TestClient

from app.main import app, init_db


def main():
    init_db()
    with TestClient(app) as client:
        health = client.get("/health")
        health.raise_for_status()

        username = "teste_backend"
        email = "teste_backend@example.com"
        password = "123456"

        response = client.post(
            "/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        if response.status_code == 409:
            response = client.post(
                "/auth/login",
                json={"username": username, "password": password},
            )
        response.raise_for_status()

        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        accounts = client.get("/accounts", headers=headers)
        categories = client.get("/categories", headers=headers)
        accounts.raise_for_status()
        categories.raise_for_status()

        account_id = accounts.json()[0]["id"]
        category_id = categories.json()[0]["id"]

        transaction = client.post(
            "/transactions",
            headers=headers,
            json={
                "description": "Teste backend",
                "value": 99.9,
                "type": "receita",
                "date": "2026-04-20",
                "account_id": account_id,
                "category_id": category_id,
            },
        )
        transaction.raise_for_status()

        sync = client.get("/sync/pull", headers=headers)
        backup = client.post("/backup", headers=headers, json={"payload": {"source": "smoke-test"}})
        sync.raise_for_status()
        backup.raise_for_status()

    print("NotaFacil backend OK")


if __name__ == "__main__":
    main()
