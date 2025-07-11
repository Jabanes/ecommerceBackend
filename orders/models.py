import uuid
from django.db import models

class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        AUTHORIZED = 'AUTHORIZED', 'Authorized'
        CAPTURED = 'CAPTURED', 'Captured'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # If you have a User model, you can use a ForeignKey.
    # For a headless setup, storing a user identifier might be sufficient initially.
    user_id = models.CharField(max_length=255, db_index=True)
    
    paypal_order_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    shopify_order_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    
    # Store the initial cart details from the frontend
    order_data = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order {self.id} for user {self.user_id} - {self.get_status_display()}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Order"
        verbose_name_plural = "Orders"
