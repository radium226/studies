from pydantic import BaseModel


type User = str


type Content = str


class Message(BaseModel):
    author: User
    content: Content

    @property
    def mentions(self) -> list[User]:
        """Extract mentioned users from the content."""
        mentions = []
        words = self.content.split()
        for word in words:
            if word.startswith("@"):
                mentions.append(word[1:])  # Remove '@' prefix
        return mentions