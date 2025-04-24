import models from '@repo/models'

export type UserProps = {
    user: models.User
}

export default function Users({ user }: UserProps) {
    return (
        <div>
            <p><em>Name:</em> {user.name}</p>
            <p>Email: {user.email}</p>
        </div>
    );
}