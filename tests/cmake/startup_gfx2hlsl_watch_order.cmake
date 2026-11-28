if(EXISTS "${INTAKE_PYTHON}")
    add_test(NAME startup_gfx2hlsl_watch_order COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/startup_gfx2hlsl_watch_order_check.py")
    set_tests_properties(startup_gfx2hlsl_watch_order PROPERTIES TIMEOUT 120)
endif()
