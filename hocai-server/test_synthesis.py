import asyncio
import httpx

async def test_synthesis():
    async with httpx.AsyncClient() as client:
        for i in range(10):
            payload = {
                "text": f"Bổ sung part {i} về Nextjs App router, Server Component, React Hook. Các kĩ thuật nâng cao như streaming. Nội dung cần giải thích cực kì chuyên sâu, không dùng từ ngũ sáo rỗng.",
                "domain": "Frontend",
                "timestamp": "2024-03-08T00:00:00Z",
                "root_concept": "Nextjs App Router",
                "conversation_id": "999"
            }
            res = await client.post("http://localhost:8000/api/mark", json=payload, timeout=300)
            print(f"Request {i}: {res.status_code}")
            
if __name__ == "__main__":
    asyncio.run(test_synthesis())
