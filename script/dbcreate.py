from core import db, stats
db.Base.metadata.create_all(db.engine)
stats.Base.metadata.create_all(stats.engine)
