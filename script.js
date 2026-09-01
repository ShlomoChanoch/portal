document.querySelector('form').addEventListener('submit', async function(event) {
  event.preventDefault();

  const containerName = document.getElementById('container_name').value;
  const containerImage = document.getElementById('container_image').value;
  const port = document.getElementById('port').value;

  const data = {
    container_name: containerName,
    container_image: containerImage,
    port: port
  };

  console.log('Dados capturados:', data);

  try {
    const response = await fetch('http://127.0.0.1:3000/api/containers', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(data)
    });

    const result = await response.json();
    console.log('Resposta do Servidor:', result);

    if (result.status === 'success') {
      alert(`Container criado com sucesso! ID: ${result.container_id}`);
    } else {
      alert(`Erro ao subir container: ${result.message}`);
    }
  } catch (error) {
    console.error('Erro na requisição:', error);
  }
});