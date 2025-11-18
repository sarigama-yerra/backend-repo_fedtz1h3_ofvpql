import os
from datetime import datetime
from typing import Optional, List, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from bson import ObjectId

from database import db, create_document
from schemas import BakeryItem


def to_serializable(doc):
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    # Convert nested ObjectIds in items if any
    if d.get("items"):
        for item in d["items"]:
            if isinstance(item.get("item_id"), ObjectId):
                item["item_id"] = str(item["item_id"])
    # Convert datetimes to isoformat
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ---------- Request models (frontend-friendly, optional fields where appropriate) ----------
class CustomerInfoIn(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    fulfillment: Literal["pickup", "delivery"] = "pickup"


class OrderItemIn(BaseModel):
    item_id: str
    quantity: int = Field(..., ge=1)


class CreateOrderRequest(BaseModel):
    items: List[OrderItemIn]
    customer: CustomerInfoIn


app = FastAPI(title="Bakery Ordering API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Bakery Ordering API running"}


# ---------------------------- ITEMS (Admin + Public list) ----------------------------
@app.get("/api/items")
def list_items(available: Optional[bool] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    query = {}
    if available is not None:
        query["available"] = available
    items = db["bakeryitem"].find(query).sort("name", 1)
    return [to_serializable(doc) for doc in items]


@app.post("/api/items", status_code=201)
def create_item(item: BakeryItem):
    collection = "bakeryitem"
    new_id = create_document(collection, item)
    doc = db[collection].find_one({"_id": ObjectId(new_id)})
    return to_serializable(doc)


@app.put("/api/items/{item_id}")
def update_item(item_id: str, item: BakeryItem):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item id")
    result = db["bakeryitem"].update_one({"_id": oid}, {"$set": item.model_dump() | {"updated_at": datetime.utcnow()}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    doc = db["bakeryitem"].find_one({"_id": oid})
    return to_serializable(doc)


@app.delete("/api/items/{item_id}", status_code=204)
def delete_item(item_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item id")
    result = db["bakeryitem"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return


# ---------------------------- ORDERS (Customer + Admin) ----------------------------
@app.post("/api/orders", status_code=201)
def place_order(payload: CreateOrderRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Verify items and compute totals based on current menu pricing
    item_ids = []
    for it in payload.items:
        try:
            item_ids.append(ObjectId(it.item_id))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid item id: {it.item_id}")

    menu_docs = list(db["bakeryitem"].find({"_id": {"$in": item_ids}, "available": True}))
    menu_by_id = {str(doc["_id"]): doc for doc in menu_docs}

    order_items = []
    total = 0.0
    for it in payload.items:
        doc = menu_by_id.get(it.item_id)
        if not doc:
            raise HTTPException(status_code=400, detail=f"Item not available: {it.item_id}")
        unit_price = float(doc.get("price", 0))
        subtotal = unit_price * it.quantity
        total += subtotal
        order_items.append({
            "item_id": it.item_id,
            "name": doc.get("name"),
            "unit_price": unit_price,
            "quantity": it.quantity,
            "subtotal": subtotal
        })

    order_number = f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    order_doc = {
        "items": order_items,
        "customer": payload.customer.model_dump(),
        "status": "pending",
        "total_amount": round(total, 2),
        "order_number": order_number,
    }

    new_id = create_document("order", order_doc)
    created = db["order"].find_one({"_id": ObjectId(new_id)})
    return to_serializable(created)


@app.get("/api/orders")
def list_orders(status: Optional[Literal["pending", "confirmed", "completed", "cancelled"]] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    query = {}
    if status:
        query["status"] = status
    orders = db["order"].find(query).sort("created_at", -1)
    return [to_serializable(o) for o in orders]


class UpdateStatusRequest(BaseModel):
    status: Literal["pending", "confirmed", "completed", "cancelled"]


@app.patch("/api/orders/{order_id}/status")
def update_order_status(order_id: str, payload: UpdateStatusRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        oid = ObjectId(order_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid order id")
    res = db["order"].update_one({"_id": oid}, {"$set": {"status": payload.status, "updated_at": datetime.utcnow()}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = db["order"].find_one({"_id": oid})
    return to_serializable(doc)


# ---------------------------- ANALYTICS (Admin) ----------------------------
@app.get("/api/analytics")
def analytics():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Totals
    total_orders = db["order"].count_documents({})
    total_revenue = sum(o.get("total_amount", 0.0) for o in db["order"].find({}))

    # Revenue by day (last 14 groups)
    pipeline = [
        {"$addFields": {"date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}}},
        {"$group": {"_id": "$date", "orders": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
        {"$sort": {"_id": 1}},
        {"$limit": 14}
    ]
    by_day = list(db["order"].aggregate(pipeline))
    by_day = [{"date": x.get("_id"), "orders": x.get("orders", 0), "revenue": x.get("revenue", 0.0)} for x in by_day]

    # Top items
    pipeline_items = [
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.name", "quantity": {"$sum": "$items.quantity"}, "revenue": {"$sum": "$items.subtotal"}}},
        {"$sort": {"quantity": -1}},
        {"$limit": 5}
    ]
    top_items = list(db["order"].aggregate(pipeline_items))
    top_items = [{"name": t.get("_id"), "quantity": t.get("quantity", 0), "revenue": t.get("revenue", 0.0)} for t in top_items]

    return {
        "total_orders": total_orders,
        "total_revenue": round(float(total_revenue), 2),
        "by_day": by_day,
        "top_items": top_items,
    }


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
