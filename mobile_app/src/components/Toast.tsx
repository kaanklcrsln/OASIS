export function Toast({ message }: { message: string }) {
  return (
    <div className="toast" role="status">
      {message}
    </div>
  );
}
