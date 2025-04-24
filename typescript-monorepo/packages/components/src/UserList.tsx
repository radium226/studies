import models from '@repo/models';
import User from './User';


export type UserListProps = {
  users: models.User[];
}

export default function UserList({ users }: UserListProps) {
  return (
    <div>
      <h2>Users</h2>
      { users.map((user) => <User key={user.email} user={user} />) }
    </div>
  );
};