"""Persistent carrier observations and auditable, human-confirmed reconciliation."""
from datetime import datetime

from app import db


class TrackingRecord(db.Model):
    __tablename__ = 'tracking_records'
    __table_args__ = (db.UniqueConstraint('shop_id', 'number', name='uq_tracking_shop_number'),
                     db.Index('ix_tracking_due', 'shop_id', 'next_check_at'),)
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False)
    number = db.Column(db.String(120), nullable=False)
    latest = db.Column(db.JSON)
    events = db.Column(db.JSON, nullable=False, default=list, server_default='[]')
    event_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    only_preinfo = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    has_movement = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    first_preinfo_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    checked_at = db.Column(db.DateTime)
    attempted_at = db.Column(db.DateTime)
    next_check_at = db.Column(db.DateTime, default=datetime.utcnow)
    error = db.Column(db.String(255))
    error_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    requested_at = db.Column(db.DateTime)
    lease_until = db.Column(db.DateTime)
    lease_token = db.Column(db.String(36))
    handled_at = db.Column(db.DateTime)
    handled_by = db.Column(db.Integer, db.ForeignKey('users.id', name='fk_tracking_handled_by_users', ondelete='SET NULL'))


class TrackingBinding(db.Model):
    __tablename__ = 'tracking_bindings'
    __table_args__ = (db.UniqueConstraint('shop_id', 'target_key', 'record_id', name='uq_tracking_target_record'),
                     db.Index('ix_tracking_binding_active', 'shop_id', 'active', 'record_id'),)
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False)
    record_id = db.Column(db.Integer, db.ForeignKey('tracking_records.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, index=True)
    after_sale_id = db.Column(db.Integer, db.ForeignKey('after_sales.id'), index=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey('shipping_parcels.id'))
    target_key = db.Column(db.String(80), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    record = db.relationship('TrackingRecord')


class TrackingRefreshJob(db.Model):
    __tablename__ = 'tracking_refresh_jobs'
    id = db.Column(db.String(36), primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False, index=True)
    scope = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='queued', server_default='queued')
    record_ids = db.Column(db.JSON, nullable=False, default=list, server_default='[]')
    done = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    failed = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    skipped = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    error = db.Column(db.String(255))
    lease_until = db.Column(db.DateTime)
    lease_token = db.Column(db.String(36))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    finished_at = db.Column(db.DateTime)


class TrackingOutboundAudit(db.Model):
    __tablename__ = 'tracking_outbound_audits'
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False)
    binding_id = db.Column(db.Integer, db.ForeignKey('tracking_bindings.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    after_sale_id = db.Column(db.Integer, db.ForeignKey('after_sales.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    evidence = db.Column(db.JSON, nullable=False)
    stock_result = db.Column(db.JSON, nullable=False, default=dict, server_default='{}')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class TrackingChange(db.Model):
    """Transactional outbox: at most one pending source change per order."""
    __tablename__ = 'tracking_changes'
    __table_args__ = (db.Index('ix_tracking_changes_due', 'updated_at', 'order_id'),)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id', ondelete='CASCADE'), primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class TrackingDiscoveryState(db.Model):
    """A once-only, bounded local history walk. New changes use the outbox."""
    __tablename__ = 'tracking_discovery_states'
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), primary_key=True)
    cursor = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    end_id = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    completed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
