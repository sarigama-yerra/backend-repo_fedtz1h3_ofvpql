"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, List

# Bakery app schemas

class BakeryItem(BaseModel):
    """
    Bakery items collection schema
    Collection name: "bakeryitem"
    """
    name: str = Field(..., description="Item name")
    description: Optional[str] = Field(None, description="Item description")
    price: float = Field(..., ge=0, description="Price in dollars")
    image_url: Optional[str] = Field(None, description="Image URL")
    category: Optional[str] = Field(None, description="Category e.g., bread, cake")
    available: bool = Field(True, description="Whether item is available")

class OrderItem(BaseModel):
    item_id: str = Field(..., description="BakeryItem _id as string")
    name: str = Field(..., description="Snapshot of item name at order time")
    price: float = Field(..., ge=0, description="Snapshot of item price at order time")
    quantity: int = Field(..., ge=1, description="Quantity ordered")

class CustomerInfo(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    address: str
    notes: Optional[str] = None

class Order(BaseModel):
    """
    Orders collection schema
    Collection name: "order"
    """
    items: List[OrderItem]
    customer: CustomerInfo
    status: str = Field("pending", description="pending, confirmed, preparing, ready, delivered, cancelled")
    total_amount: float = Field(..., ge=0)
    payment_status: str = Field("unpaid", description="unpaid, paid, refunded")

# Legacy example schemas kept for reference, not used by bakery app
class User(BaseModel):
    name: str
    email: str
    address: str
    age: Optional[int] = None
    is_active: bool = True

class Product(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    category: str
    in_stock: bool = True
