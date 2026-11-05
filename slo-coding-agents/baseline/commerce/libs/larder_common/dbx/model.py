"""
Mini ORM.

    class Product(Model):
        __table__ = "products"
        id = Field(int, pk=True)
        sku = Field(str)

    Product.get(1)                       -> Product or None
    Product.get_by(sku="SKU-0001")       -> Product or None
    Product.filter(category="dairy")     -> [Product]
    Product.create(sku=..., ...)         -> Product
    p.save() / p.delete()

Rows are loaded eagerly; relationships are just methods on the models
(see each service's models.py). There is no identity map and no caching, so
every call is a query.

Each model says which database it lives in with ``__database__`` (a name
registered with larder_common.dbx). If you forget, you get "default", which
is a file called default.db in the data dir. Ask me how I know. (TH 2023)
"""
from larder_common.dbx import connection as _connection


class Field(object):
    def __init__(self, type_=str, pk=False, default=None, column=None, null=True):
        self.type_ = type_
        self.pk = pk
        self.default = default
        self.column = column
        self.null = null
        self.name = None

    def to_python(self, value):
        if value is None:
            return None
        if self.type_ is bool:
            return bool(value)
        if self.type_ in (int, float, str):
            try:
                return self.type_(value)
            except (TypeError, ValueError):
                return value
        return value

    def to_db(self, value):
        if value is None:
            return None
        if self.type_ is bool:
            return 1 if value else 0
        return value


class ModelMeta(type):
    def __new__(mcs, name, bases, attrs):
        fields = {}
        for base in bases:
            fields.update(getattr(base, "_fields", {}))
        for key, val in list(attrs.items()):
            if isinstance(val, Field):
                val.name = key
                if val.column is None:
                    val.column = key
                fields[key] = val
                del attrs[key]
        attrs["_fields"] = fields
        pk = [f for f in fields.values() if f.pk]
        attrs["_pk"] = pk[0] if pk else None
        return super().__new__(mcs, name, bases, attrs)


class DoesNotExist(Exception):
    pass


class Model(object, metaclass=ModelMeta):
    __table__ = None
    __database__ = "default"

    @classmethod
    def _db(cls):
        return _connection.db(cls.__database__)

    def __init__(self, **kwargs):
        for name, field in self._fields.items():
            default = field.default() if callable(field.default) else field.default
            setattr(self, name, kwargs.get(name, default))
        self._persisted = False

    def __repr__(self):
        pk = getattr(self, self._pk.name) if self._pk else "?"
        return "<%s %s>" % (self.__class__.__name__, pk)

    def __getitem__(self, item):
        # old code treats models like rows
        return getattr(self, item)

    # --- loading ---------------------------------------------------------

    @classmethod
    def _from_row(cls, row):
        if row is None:
            return None
        obj = cls.__new__(cls)
        keys = row.keys()
        for name, field in cls._fields.items():
            setattr(obj, name, field.to_python(row[field.column]) if field.column in keys else None)
        obj._persisted = True
        return obj

    @classmethod
    def _where(cls, kwargs):
        clauses, params = [], []
        for key, val in kwargs.items():
            op = "="
            if "__" in key:
                key, op_name = key.split("__", 1)
                op = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "ne": "!=", "like": "LIKE", "in": "IN"}[op_name]
            col = cls._fields[key].column if key in cls._fields else key
            if op == "IN":
                vals = list(val)
                if not vals:
                    clauses.append("1=0")
                    continue
                clauses.append("%s IN (%s)" % (col, ",".join("?" * len(vals))))
                params.extend(vals)
            elif val is None and op == "=":
                clauses.append("%s IS NULL" % col)
            else:
                clauses.append("%s %s ?" % (col, op))
                params.append(val)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    @classmethod
    def get(cls, pk):
        row = cls._db().query_one("SELECT * FROM %s WHERE %s = ?" % (cls.__table__, cls._pk.column), (pk,))
        return cls._from_row(row)

    @classmethod
    def get_or_404(cls, pk):
        obj = cls.get(pk)
        if obj is None:
            raise DoesNotExist("%s %r" % (cls.__name__, pk))
        return obj

    @classmethod
    def get_by(cls, **kwargs):
        where, params = cls._where(kwargs)
        row = cls._db().query_one("SELECT * FROM %s%s LIMIT 1" % (cls.__table__, where), params)
        return cls._from_row(row)

    @classmethod
    def filter(cls, order_by=None, limit=None, offset=None, **kwargs):
        where, params = cls._where(kwargs)
        sql = "SELECT * FROM %s%s" % (cls.__table__, where)
        if order_by:
            desc = order_by.startswith("-")
            sql += " ORDER BY %s%s" % (order_by.lstrip("-"), " DESC" if desc else "")
        if limit is not None:
            sql += " LIMIT %d" % int(limit)
            if offset:
                sql += " OFFSET %d" % int(offset)
        return [cls._from_row(r) for r in cls._db().query(sql, params)]

    @classmethod
    def all(cls, order_by=None):
        return cls.filter(order_by=order_by)

    @classmethod
    def count(cls, **kwargs):
        where, params = cls._where(kwargs)
        return cls._db().scalar("SELECT COUNT(*) FROM %s%s" % (cls.__table__, where), params, 0)

    @classmethod
    def raw(cls, sql, params=()):
        return [cls._from_row(r) for r in cls._db().query(sql, params)]

    # --- writing ---------------------------------------------------------

    @classmethod
    def create(cls, **kwargs):
        obj = cls(**kwargs)
        obj.save()
        return obj

    def _values(self, include_pk=True):
        out = {}
        for name, field in self._fields.items():
            if field.pk and not include_pk:
                continue
            out[field.column] = field.to_db(getattr(self, name))
        return out

    def save(self):
        pk = self._pk
        pk_val = getattr(self, pk.name) if pk else None
        if self._persisted and pk is not None:
            vals = self._values(include_pk=False)
            sets = ", ".join("%s = ?" % c for c in vals)
            self._db().execute(
                "UPDATE %s SET %s WHERE %s = ?" % (self.__table__, sets, pk.column),
                list(vals.values()) + [pk_val],
            )
        else:
            vals = self._values(include_pk=pk_val is not None)
            cols = ", ".join(vals.keys())
            marks = ", ".join("?" * len(vals))
            rowid = self._db().execute("INSERT INTO %s (%s) VALUES (%s)" % (self.__table__, cols, marks), list(vals.values()))
            if pk is not None and pk_val is None and pk.type_ is int:
                setattr(self, pk.name, rowid)
            self._persisted = True
        return self

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self.save()

    def delete(self):
        self._db().execute("DELETE FROM %s WHERE %s = ?" % (self.__table__, self._pk.column), (getattr(self, self._pk.name),))
        self._persisted = False

    def to_dict(self):
        return {name: getattr(self, name) for name in self._fields}
