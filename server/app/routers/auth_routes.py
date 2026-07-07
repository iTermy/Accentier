from fastapi import APIRouter
from pydantic import BaseModel

from .. import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(body: Credentials):
    user_id = auth.create_user(body.username, body.password)
    token = auth.issue_token(user_id)
    return {"token": token, "username": body.username.strip()}


@router.post("/login")
def login(body: Credentials):
    token = auth.login(body.username, body.password)
    return {"token": token, "username": body.username.strip()}


@router.get("/me")
def me(user: dict = auth.CurrentUser):
    return user
