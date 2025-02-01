const MAX_MEMBERS_FOR = {
  Arte: {
    Joya: 3,
    Domolactico: 2,
    Serpiente: 10,
  },
  Camps: {
    "Cabra Camp": 2,
    Cafeteria: 2,
    "Camp Tafies": 9,
    Dorado: 4,
    "El Correo": 5,
    "Entre Pares": 4,
    "Familia Natural Camp": 8,
    Fresh: 8,
    "HORUS ": 11,
    "Industria Fuegina": 3,
    "La Cantina": 7,
    Pi: 7,
    "Pies en Libertad": 10,
    "Planta Base Camp": 7,
    Pochocamp: 7,
    Purmalandia: 12,
    "Sindicato de Sentidos": 6,
    vän: 0,
  },
  Templo: {
    "Aiko 2025": 15,
  },
}
const MEMBER_FIELDS = ["Nombre", "Apellido", "DNI", "Telefono", "Dia"] // [field="Nombre"]

const campsDropdownEl = document.querySelector('[field="Grupo"][parent="Camps"]')
Object.keys(MAX_MEMBERS_FOR.Camps).forEach(camp => {
  const optionEl = document.createElement("option")
  optionEl.value = camp
  optionEl.textContent = camp
  campsDropdownEl.appendChild(optionEl)
})

const arteDropdownEl = document.querySelector('[field="Grupo"][parent="Arte"]')
Object.keys(MAX_MEMBERS_FOR.Arte).forEach(arte => {
  const optionEl = document.createElement("option")
  optionEl.value = arte
  optionEl.textContent = arte
  arteDropdownEl.appendChild(optionEl)
})

const temploDropdownEl = document.querySelector('[field="Grupo"][parent="Templo"]')
Object.keys(MAX_MEMBERS_FOR.Templo).forEach(templo => {
  const optionEl = document.createElement("option")
  optionEl.value = templo
  optionEl.textContent = templo
  temploDropdownEl.appendChild(optionEl)
})

const formEl = document.querySelector(".js-form")
const memberHTML = document.querySelector(".js-memberHTML").innerHTML

const StateMachine = (reducer, render) => {
  let state = reducer()

  const dispatch = action => {
    state = reducer(JSON.parse(JSON.stringify(state)), action)
    state.lastAction = action.type
    render(state)
  }

  const getState = () => state

  return { getState, dispatch }
}

const LOCAL_STORAGE_KEY = "prePostEventFormState"

const initialState = () => {
  const persistedState = localStorage.getItem(LOCAL_STORAGE_KEY)
  if (persistedState) return JSON.parse(persistedState)

  return {
    lastAction: null,
    area: "",
    grupo: "",
    descripcion: "",
    members: [],
  }
}

const reducer = (state = initialState(), action = {}) => {
  console.log({ state, action })

  switch (action.type) {
    case "SET_AREA":
      state.area = action.area
      state.grupo = ""
      state.members = []
      return state
    case "SET_GRUPO":
      state.grupo = action.grupo
      state.members = []
      if (state.members.length < MAX_MEMBERS_FOR[state.area][state.grupo]) state.members.push({})
      return state
    case "SET_DESCRIPCION":
      state.descripcion = action.descripcion
      return state
    case "SET_MEMBER_VALUE":
      state.members[state.members.length - 1][action.payload.field] = action.payload.value
      return state
    case "REMOVE_MEMBER":
      state.members.pop()
      return state
    case "ADD_MEMBER":
      state.members = state.members.map(member => ({ ...member, isSubmitted: true }))
      if (state.members.length < MAX_MEMBERS_FOR[state.area][state.grupo]) state.members.push({})
      return state
    case "SUBMIT_FORM":
      state.isFormFinished = true
      return state
  }

  return state
}

const disableEl = el => {
  el.style.backgroundColor = "var(--bs-secondary-bg)"
  el.style.pointerEvents = "none"
}

const renderArea = (state, doc) => {
  const areaEl = doc.querySelector('[field="Area"]')
  areaEl.value = state.area
  if (state.members.some(member => member.isSubmitted) || state.isFormFinished) {
    disableEl(areaEl)
  }
}

const renderGrupo = (state, doc) => {
  doc.querySelectorAll('[field="Grupo"]').forEach(el => {
    el.closest(".form-group").style.display = "none"
    el.disabled = true
  })
  if (!state.area) return

  const grupoEl = doc.querySelector(`[field="Grupo"][parent="${state.area}"]`)
  grupoEl.value = state.grupo
  grupoEl.disabled = false
  if (state.members.some(member => member.isSubmitted) || state.isFormFinished) {
    disableEl(grupoEl)
  }
  grupoEl.closest(".form-group").style.display = "block"
}

const renderMembers = (state, doc) => {
  const userInfoPlusSubmitEl = doc.querySelector(".js-userInfoPlusSubmit")
  userInfoPlusSubmitEl.style.display = state.grupo ? "block" : "none"
  if (!state.grupo) return

  const campsUserInfoEl = doc.querySelector(".js-campsUserInfo")
  campsUserInfoEl.innerHTML = ""

  state.members.forEach(member => {
    const tempDiv = document.createElement("div")
    tempDiv.innerHTML = memberHTML
    Object.entries(member).forEach(([field, value]) => {
      if (!MEMBER_FIELDS.includes(field)) return
      const input = tempDiv.querySelector(`[field="${field}"]`)
      if (field === "Dia") {
        const option = input.querySelector(`option[value="${value}"]`)
        option.setAttribute("selected", "selected")
      } else {
        input.setAttribute("value", value || "")
      }
    })

    if (member.isSubmitted || state.isFormFinished) {
      tempDiv.querySelectorAll("input,select").forEach(el => el.setAttribute("disabled", "disabled"))
    } else {
      tempDiv.querySelector(".js-removeMember").classList.remove("d-none")
      tempDiv.querySelector(".js-addMember").classList.remove("d-none")
    }

    campsUserInfoEl.insertAdjacentHTML("beforeend", tempDiv.innerHTML)
  })

  // if all members are submitted, and there are less than the max number of members, add a button to add another member
  // this happens when user removes last member
  const isAllMembersSubmitted = state.members.every(member => member.isSubmitted)
  const isMaxMembers = state.members.length === MAX_MEMBERS_FOR[state.area][state.grupo]
  if (isAllMembersSubmitted && !isMaxMembers && !state.isFormFinished) {
    campsUserInfoEl.insertAdjacentHTML(
      "beforeend",
      `<button type="button" class="js-addMember btn btn-primary mb-3 align-self-end">Agregar otrx</button>`,
    )
  }
  if (isMaxMembers && !state.isFormFinished) {
    const lastAddMemberEl = [...campsUserInfoEl.querySelectorAll(".js-addMember")].at(-1)
    if (lastAddMemberEl) lastAddMemberEl.textContent = "Enviar"
  }
}

const renderThankYouMessage = state => {
  const thankYouContainerEl = document.getElementById("thankYouContainer")
  if (!state.area || !state.grupo) return

  const isAllMembersSubmitted = state.members.every(member => member.isSubmitted)
  const isMaxMembers = state.members.length === MAX_MEMBERS_FOR[state.area][state.grupo]
  if ((isAllMembersSubmitted && isMaxMembers) || state.isFormFinished) {
    document.querySelectorAll('[type="submit"]').forEach(el => el.classList.add("d-none"))
    thankYouContainerEl.style.display = "block"
  }
}

const persistToLocalStorage = state => localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(state))

const render = state => {
  console.log(state)

  const doc = document.createElement("div")
  doc.innerHTML = formEl.outerHTML

  renderArea(state, doc)
  renderGrupo(state, doc)
  renderMembers(state, doc)

  morphdom(formEl, doc.children[0])

  renderThankYouMessage(state, doc)

  persistToLocalStorage(state)
}

const formMachine = StateMachine(reducer, render)
render(formMachine.getState())

// DOM events
document
  .querySelector('[field="Area"]')
  .addEventListener("change", event => formMachine.dispatch({ type: "SET_AREA", area: event.target.value }))

document
  .querySelectorAll('[field="Grupo"]')
  .forEach(el => el.addEventListener("change", event => formMachine.dispatch({ type: "SET_GRUPO", grupo: event.target.value })))

document
  .querySelector('[field="Descripcion"]')
  ?.addEventListener("change", event => formMachine.dispatch({ type: "SET_DESCRIPCION", descripcion: event.target.value }))

document.addEventListener("input", event => {
  const field = event.target.getAttribute("field")
  const isMemberField = MEMBER_FIELDS.includes(field)
  if (!isMemberField) return

  formMachine.dispatch({ type: `SET_MEMBER_VALUE`, payload: { field, value: event.target.value } })
})

formEl.addEventListener("click", event => {
  if (!event.target.classList.contains("js-addMember")) return
  if (!formValid()) {
    event.preventDefault()
    alert("Por favor complete todos los campos requeridos: Área, Grupo, Datos de las personas (Nombre, DNI, Teléfono y Día de ingreso).")
    return
  }
  setTimeout(() => formMachine.dispatch({ type: "ADD_MEMBER" }), 100)
})

// remove last added member
formEl.addEventListener("click", event => {
  if (!event.target.matches(".js-removeMember")) return
  event.preventDefault()
  formMachine.dispatch({ type: "REMOVE_MEMBER" })
})

// validation
const formValid = () => {
  const { area, grupo, members } = formMachine.getState()
  const membersValid = members.every(member => member.Nombre?.trim() && member.DNI?.trim() && member.Dia?.trim())
  return area.trim() && grupo.trim() && membersValid
}

// prevent form submission on ENTER keypress
formEl.addEventListener("keydown", event => {
  if (event.key === "Enter") event.preventDefault()
})

// prevent form submission on submit button click unless form is valid
formEl.addEventListener("click", event => {
  const submitEl = event.target
  if (!submitEl.matches('button[type="submit"], input[type="submit"]')) return
  if (submitEl.classList.contains("js-addMember")) return

  if (!formValid()) {
    event.preventDefault()
    alert("Por favor complete todos los campos requeridos: Área, Grupo, Datos de las personas (Nombre, DNI, Teléfono y Día de ingreso).")
    return
  }

  if (formMachine.getState().members.every(member => member.isSubmitted)) {
    event.preventDefault()
  }

  setTimeout(() => {
    formMachine.dispatch({ type: "SUBMIT_FORM" })
    document.querySelector("#thankYouContainer").style.display = "block"
    submitEl.classList.add("disabled")
    submitEl.value = "Enviado!"
  }, 100)
})
