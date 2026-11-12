
import os
import sqlite3

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="Order Service",
    description="Creates and manages food orders",
    version="1.0.0"
)

# Restaurant Service URL
# Local: http://localhost:8001
# Kubernetes: http://restaurant-service:8001
RESTAURANT_URL = os.getenv(
    "RESTAURANT_URL",
    "http://localhost:8001"
)

# SQLite database
DATABASE = os.getenv("DATABASE", "orders.db")


# Initialize database
def init_db():
    conn = sqlite3.connect(DATABASE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            total_price REAL NOT NULL,
            status TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_db()


# Order request model
class OrderRequest(BaseModel):
    restaurant_id: int = Field(gt=0)
    item_name: str = Field(min_length=1)
    quantity: int = Field(gt=0)


# Root endpoint
@app.get("/")
def root():
    return {
        "message": "Order Service is running",
        "service": "order-service"
    }


# Health endpoint
@app.get("/health")
def health_check():
    return {
        "status": "UP",
        "service": "order-service"
    }


# Create order
@app.post("/orders")
async def create_order(order: OrderRequest):

    # Step 1: Get restaurant details
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:

            response = await client.get(
                f"{RESTAURANT_URL}/restaurants/{order.restaurant_id}"
            )

            response.raise_for_status()

            restaurant = response.json()

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=503,
            detail="Restaurant service request timed out"
        )

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Restaurant not found"
            )

        raise HTTPException(
            status_code=503,
            detail="Restaurant service returned an error"
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Restaurant service unavailable"
        )

    # Step 2: Validate menu item
    menu = restaurant.get("menu", [])

    menu_item = next(
        (
            item for item in menu
            if item.get("item_name", item.get("name", "")).strip().lower()
            == order.item_name.strip().lower()
        ),
        None
    )

    if not menu_item:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Menu item not found",
                "requested_item": order.item_name,
                "available_items": [
                    item.get("item_name", item.get("name"))
                    for item in menu
                ]
            }
        )

    # Step 3: Calculate order total
    total_price = menu_item["price"] * order.quantity

    # Step 4: Save order in SQLite
    conn = sqlite3.connect(DATABASE)

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO orders (
                restaurant_id,
                item_name,
                quantity,
                total_price,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                order.restaurant_id,
                menu_item.get("item_name", order.item_name),
                order.quantity,
                total_price,
                "CONFIRMED"
            )
        )

        order_id = cursor.lastrowid

        conn.commit()

    finally:
        conn.close()

    # Step 5: Return order confirmation
    return {
        "order_id": order_id,
        "restaurant_id": restaurant["id"],
        "restaurant": restaurant["name"],
        "item_name": menu_item.get("item_name", order.item_name),
        "quantity": order.quantity,
        "unit_price": menu_item["price"],
        "total_price": total_price,
        "status": "CONFIRMED"
    }


# Get all orders
@app.get("/orders")
def get_orders():

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders ORDER BY id DESC")

        orders = [
            dict(row) for row in cursor.fetchall()
        ]

        return orders

    finally:
        conn.close()


# Get individual order
@app.get("/orders/{order_id}")
def get_order(order_id: int):

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        )

        order = cursor.fetchone()

        if not order:
            raise HTTPException(
                status_code=404,
                detail="Order not found"
            )

        return dict(order)

    finally:
        conn.close()
