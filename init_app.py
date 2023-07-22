from sqlalchemy.exc import OperationalError
import os

with open('secret', 'wb') as f:
    f.write(os.urandom(32))

from app import app, db

run = True
while run:
    try:
        with app.app_context():
            db.create_all()

            run = False
    except OperationalError:
        pass  # This will happen if the database is not started
