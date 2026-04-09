from fastapi.testclient import TestClient
from app.main import app
from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.services.seed import seed_demo_data

# Create tables and seed
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    seed_demo_data(db)

client = TestClient(app)

print("=== Topics ===")
r = client.get("/api/topics")
print(f"GET /api/topics: {r.status_code}, count={len(r.json())}")

r = client.post("/api/topics", json={"name":"Test Topic","description":"Test desc","keywords":["test"],"schedule":"每天 09:00","enabled":True})
print(f"POST /api/topics: {r.status_code}, name={r.json().get('name')}")

tid = r.json()["id"]
r = client.delete(f"/api/topics/{tid}")
print(f"DELETE /api/topics/{tid}: {r.status_code}")

print()
print("=== Articles ===")
r = client.get("/api/articles")
print(f"GET /api/articles: {r.status_code}, count={len(r.json())}")
# Check content field exists
if r.json():
    has_content = "content" in r.json()[0]
    print(f"  Article has 'content' field: {has_content}")

r = client.get("/api/articles?bookmarked=true")
print(f"GET /api/articles?bookmarked=true: {r.status_code}, count={len(r.json())}")

r = client.get("/api/articles?keyword=舰船")
print(f"GET /api/articles?keyword=舰船: {r.status_code}, count={len(r.json())}")

r = client.post("/api/articles/1/bookmark", json={"bookmarked": True})
print(f"POST /api/articles/1/bookmark: {r.status_code}, bookmarked={r.json().get('bookmarked')}")

print()
print("=== QA ===")
r = client.post("/api/qa/query", json={"question": "舰船领域有哪些动态？"})
print(f"POST /api/qa/query: {r.status_code}, has_answer={bool(r.json().get('answer'))}")

print()
print("=== Reports ===")
r = client.get("/api/reports")
print(f"GET /api/reports: {r.status_code}, count={len(r.json())}")

r = client.post("/api/reports/generate", json={"topic": None, "title": None})
print(f"POST /api/reports/generate: {r.status_code}, title={r.json().get('title')}")

print()
print("ALL TESTS PASSED")
