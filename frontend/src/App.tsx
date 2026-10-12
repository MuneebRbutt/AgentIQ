import { useEffect, useState } from "react";
import "./App.css";

type HealthResponse = {
  api: string;
  database: string;
};

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    const apiUrl = import.meta.env.VITE_API_URL;

    fetch(`${apiUrl}/api/v1/health`)
      .then((response) => {
        if (!response.ok) {
          throw new Error("Backend health check failed");
        }

        return response.json();
      })
      .then((data: HealthResponse) => {
        setHealth(data);
      })
      .catch((requestError: Error) => {
        setError(requestError.message);
      });
  }, []);

  return (
    <main>
      <h1>AgentIQ</h1>
      <p>Technical foundation status</p>

      {error && <p>Connection error: {error}</p>}

      {!health && !error && <p>Checking services...</p>}

      {health && (
        <section>
          <p>Backend API: {health.api}</p>
          <p>PostgreSQL: {health.database}</p>
        </section>
      )}
    </main>
  );
}

export default App;