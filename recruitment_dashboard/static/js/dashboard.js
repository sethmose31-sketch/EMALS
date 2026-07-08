$(document).ready(function() {
  // initialize DataTable
  $('#applicants-table').DataTable({
    ajax: { url: '/api/applicants', dataSrc: 'data' },
    columns: [
      { data: 'name', render: function(d, type, row){ return '<a href="/applicant/'+row.id+'">'+(d||'—')+'</a>'; } },
      { data: 'title' },
      { data: 'experience' },
      { data: 'location' },
      { data: 'source_file' },
      { data: 'id', render: function(d){ return '<a class="btn btn-sm btn-primary" href="/applicant/'+d+'">View</a>'; } }
    ],
    pageLength: 25,
    deferRender: true,
  });

  // render skills chart
  const labels = topSkills.map(s => s[0]);
  const values = topSkills.map(s => s[1]);
  const ctx = document.getElementById('skillsChart').getContext('2d');
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Top skills',
        data: values,
        backgroundColor: 'rgba(13,110,253,0.8)'
      }]
    },
    options: { responsive: true, maintainAspectRatio: false }
  });
});
